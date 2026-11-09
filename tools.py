"""Tool handlers and the brief builder.

Every handler takes the model's ``args`` dict and returns a JSON string, the
Hermes convention. Each one talks only to the Tensorust API through the shared
:class:`Screener` and degrades to the bundled snapshot when the API is away.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .client import TensorustUnavailable
from .lists import LIST_RULES
from .screen import SEVERITY_ORDER, Screener, Screening, list_label

ONTOLOGY_URL = "https://shop.tensorust.com/ontology"
_ADDRESS_RE = re.compile(r"^\s*\d+\s+\S.*?,?\s+(\d{5})(?:-\d{4})?\s*$")


class ToolContext:
    """What the handlers need from the plugin: a screener and a place to log."""

    def __init__(self, screener: Screener):
        self.screener = screener


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error(message: str, **extra: Any) -> str:
    return _json({"error": message, **extra})


def _name_arg(args: dict) -> Optional[str]:
    name = args.get("name") if isinstance(args, dict) else None
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()[:200]


# -- handlers -----------------------------------------------------------------------------------------

def cp_screen(ctx: ToolContext, args: dict, **_: Any) -> str:
    name = _name_arg(args)
    if not name:
        return _error("name is required")
    amount = args.get("amount")
    try:
        amount = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    screening = ctx.screener.screen(name, description=str(args.get("description") or ""), amount=amount,
                                    date=str(args.get("date") or ""))
    payload = screening.as_dict()
    payload["summary"] = summary_line(screening)
    payload["next_step"] = next_step(screening)
    return _json(payload)


def cp_lookup(ctx: ToolContext, args: dict, **_: Any) -> str:
    name = _name_arg(args)
    if not name:
        return _error("name is required")
    try:
        hops = int(args.get("hops") or 3)
    except (TypeError, ValueError):
        hops = 3
    hops = max(0, min(hops, 5))
    try:
        card = ctx.screener.client.call("lookup_organization", {"name": name}, full=True)
    except TensorustUnavailable as exc:
        return _error("Tensorust API unreachable", detail=str(exc), offline=True)
    if not card.get("found"):
        return _json({"found": False, "name": name, "other_matches": card.get("other_matches") or [],
                      "note": "No organization under that name in the ontology. Try the legal name or a domain."})
    chain = ownership_chain(ctx, card, hops)
    return _json({
        "found": True, "oid": card.get("oid"), "legal_name": card.get("name"), "type": card.get("type"),
        "sources": [s for s in card.get("sources") or [] if s], "links": card.get("links") or {},
        "obligations": card.get("obligations") or [], "props": card.get("props") or {},
        "ownership_chain": chain, "other_matches": card.get("other_matches") or [],
        "access": card.get("access"), "ontology": ONTOLOGY_URL,
    })


def cp_credit(ctx: ToolContext, args: dict, **_: Any) -> str:
    name = _name_arg(args)
    if not name:
        return _error("name is required")
    try:
        card = ctx.screener.client.call("lender_marks", {"name": name}, full=True)
    except TensorustUnavailable as exc:
        return _error("Tensorust API unreachable", detail=str(exc), offline=True)
    return _json(card)


def cp_who_owns_address(ctx: ToolContext, args: dict, **_: Any) -> str:
    address = str(args.get("address") or "").strip()
    zip_code = str(args.get("zip") or "").strip()
    if not address or not re.fullmatch(r"\d{5}", zip_code):
        return _error("address and a 5-digit zip are required")
    try:
        card = ctx.screener.client.call("who_owns_home", {"address": address, "zip": zip_code}, full=True)
    except TensorustUnavailable as exc:
        return _error("Tensorust API unreachable", detail=str(exc), offline=True)
    return _json(card)


def cp_brief(ctx: ToolContext, args: dict, **_: Any) -> str:
    name = _name_arg(args)
    if not name:
        return _error("name is required")
    context = str(args.get("context") or "")
    markdown, severity = build_brief(ctx, name, context)
    return _json({"name": name, "severity": severity, "markdown": markdown})


# -- shared pieces --------------------------------------------------------------------------------------

def ownership_chain(ctx: ToolContext, card: dict, hops: int) -> list[dict]:
    """Follow SUBSIDIARY_OF / OWNED_BY style links upward, one preview per hop."""
    chain: list[dict] = []
    current = card
    seen = {card.get("oid")}
    for _ in range(hops):
        parent = _parent_preview(current)
        if not parent or parent.get("oid") in seen:
            break
        seen.add(parent.get("oid"))
        chain.append({"oid": parent.get("oid"), "name": parent.get("name"), "type": parent.get("type")})
        try:
            current = ctx.screener.client.call("get_object", {"id": parent["oid"]})
        except (TensorustUnavailable, KeyError):
            break
    return chain


def _parent_preview(card: dict) -> Optional[dict]:
    links = card.get("links") or {}
    for key in ("~SUBSIDIARY_OF", "SUBSIDIARY_OF", "PARENT", "CONTROLLED_BY", "OWNED_BY", "MEMBER_OF"):
        link = links.get(key)
        if isinstance(link, dict):
            for preview in link.get("preview") or []:
                if isinstance(preview, dict) and preview.get("oid"):
                    return preview
    return None


def summary_line(screening: Screening) -> str:
    if not screening.hit:
        if screening.offline:
            return (f"{screening.name}: no match in the bundled list snapshot; the Tensorust API was unreachable, "
                    f"so sanctions and ownership were not checked.")
        org = screening.organization or {}
        legal = f" ({org.get('legal_name')})" if org.get("found") and org.get("legal_name") else ""
        return f"{screening.name}{legal}: not on the screened lists, no OFAC name match."
    parts = []
    for finding in screening.findings:
        labels = [list_label(l) for l in finding.lists]
        status = "" if finding.status == "listed" else f" ({finding.status})"
        parts.append(f"{finding.vendor_name}{status}: {', '.join(labels)}")
    return f"{screening.name}: {screening.severity.upper()}. " + "; ".join(parts)


def next_step(screening: Screening) -> str:
    sev = screening.severity
    if sev == "prohibited":
        return "Stop. This purchase or payment is barred under the cited rule. Do not proceed without counsel."
    if sev == "review":
        return ("Pause before paying, emailing a PO or signing. Confirm the funding source and the exact entity, "
                "and show the user the citations above.")
    if sev == "watch":
        return "Proceed with care: note the listing in the record and confirm the entity matches."
    return "Clear on the screened lists. Keep the screening date with the record."


def build_brief(ctx: ToolContext, name: str, context: str = "") -> tuple[str, str]:
    screening = ctx.screener.screen(name, description=context)
    org = screening.organization or {}
    lines = [f"# Counterparty brief: {name}", ""]
    if context:
        lines += [f"Context: {context}", ""]
    lines += [f"**Verdict: {screening.severity.upper()}.** {next_step(screening)}", ""]

    lines.append("## Identity")
    if org.get("found"):
        lines.append(f"- Legal name: {org.get('legal_name')} (`{org.get('oid')}`)")
        if org.get("country") or org.get("state"):
            lines.append(f"- Location: {', '.join(str(x) for x in (org.get('state'), org.get('country')) if x)}")
        if org.get("ticker"):
            lines.append(f"- Ticker: {org.get('ticker')}")
        sources = org.get("sources") or []
        lines.append("- Public-record sources: " + (", ".join(sources) if sources else "list notices only"))
    elif screening.offline:
        lines.append("- Tensorust API unreachable; identity not resolved. Findings below come from the bundled snapshot.")
    else:
        lines.append("- No organization card under this name. The list findings below match on the name itself.")
    lines.append("")

    lines.append("## Lists and sanctions")
    if screening.findings:
        for finding in screening.findings:
            for flag in sorted(finding.flags, key=lambda f: SEVERITY_ORDER.get(f.get("severity", "watch"), 0),
                               reverse=True):
                label = list_label(flag.get("list") or "", str(flag.get("rule") or ""))
                status = "" if finding.status == "listed" else f", {finding.status}"
                lines.append(f"- **{finding.vendor_name}** on {label} ({flag.get('severity', 'watch')}{status}). "
                             f"{(flag.get('why') or '').strip()} Source: {flag.get('citation') or 'see list notice'}.")
    else:
        lines.append("- Not on the DoD 1260H list, FCC Covered List, Section 889, UFLPA Entity List or BIOSECURE list; "
                     "no OFAC name match.")
    lines.append("")

    chain: list[dict] = []
    if org.get("found"):
        try:
            card = ctx.screener.client.call("lookup_organization", {"name": name})
            chain = ownership_chain(ctx, card, 3)
        except TensorustUnavailable:
            chain = []
    lines.append("## Ownership chain")
    if chain:
        path = " -> ".join([str(org.get("legal_name"))] + [str(c.get("name")) for c in chain])
        lines.append(f"- {path}")
    elif org.get("found"):
        links = org.get("links") or {}
        if links:
            lines.append("- No parent link on record. Linked records: " + ", ".join(
                f"{k.lstrip('~')} ({v.get('count')})" for k, v in links.items()))
        else:
            lines.append("- No ownership links on record.")
    else:
        lines.append("- Not available.")
    lines.append("")

    lines.append("## Private credit marks")
    credit = None
    if not screening.offline:
        try:
            credit = ctx.screener.client.call("lender_marks", {"name": name})
        except TensorustUnavailable:
            credit = None
    if credit and credit.get("found"):
        lines.append(f"- {credit.get('read')} Loan par ${credit.get('loan_par_usd'):,}, maturity "
                     f"{credit.get('maturity')}, as of {credit.get('as_of')}. Source: {credit.get('source')}.")
    else:
        lines.append("- No BDC-held first-lien loan on record (not private-credit financed, or not in SEC BDC filings).")
    lines.append("")

    pulled = (screening.lists_pulled or "")[:10]
    lines.append("## Sources")
    lines.append(f"- Tensorust public-records ontology ({ONTOLOGY_URL}); lists pulled {pulled or 'see snapshot'}.")
    for list_id in ("1260H", "FCC_COVERED", "SEC889", "UFLPA", "BIOSECURE"):
        meta = ctx.screener.index.list_meta(list_id)
        if meta.get("name"):
            lines.append(f"- {meta['name']}: {meta.get('operator') or ''}; {meta.get('source') or ''}".rstrip("; "))
    if screening.offline:
        lines.append("- The Tensorust API was unreachable while this brief was built; re-run for sanctions and ownership.")
    return "\n".join(lines), screening.severity


def looks_like_address(text: str) -> Optional[tuple[str, str]]:
    match = _ADDRESS_RE.match(text or "")
    if not match:
        return None
    zip_code = match.group(1)
    address = text.strip()[: match.start(1)].rstrip(" ,")
    return address, zip_code
