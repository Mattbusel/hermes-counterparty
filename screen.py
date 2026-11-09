"""Screening logic: one name in, a cited finding out.

A screening combines three things:

1. the local alias index over the bundled list snapshot (always available),
2. ``exposure_screen_purchases`` on the Tensorust API, which matches the name
   against the tracked vendor groups and returns the rule, severity and
   citation for each list it sits on as of today,
3. ``lookup_organization``, which returns the organization card: legal name,
   public-record sources, ownership links and an OFAC sanctions match.

If the API does not answer inside the time budget, the result is marked
``offline`` and carries the local findings with the statutory citations from
``lists.LIST_RULES``. Offline never silently clears a name: it says so.
"""

from __future__ import annotations

import concurrent.futures
import datetime as _dt
import threading
from typing import Iterable, Optional

from .client import TensorustClient, TensorustUnavailable
from .lists import LIST_RULES, ListIndex, VendorHit

SEVERITY_ORDER = {"clear": 0, "watch": 1, "review": 2, "prohibited": 3}
_OFAC_RULE = "OFAC-SDN"
# The API names flags by rule; the plugin groups by list. ASDA (American Security Drone Act)
# flags ride on the 1260H/FCC drone listings, so they are labelled by their own rule id.
RULE_TO_LIST = {"889-GRANTS": "SEC889", "889-CONTRACTS": "SEC889", "FCC-COVERED": "FCC_COVERED",
                "BIOSECURE-851": "BIOSECURE", "UFLPA-IMPORT": "UFLPA", "1260H": "1260H", "OFAC-SDN": "OFAC"}


def list_label(list_id: str, rule: str = "") -> str:
    if list_id == "OFAC":
        return "OFAC sanctions list"
    if list_id in LIST_RULES:
        return LIST_RULES[list_id]["label"]
    return rule or list_id or "a screened list"
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="counterparty")


def worst(*levels: str) -> str:
    return max((lvl for lvl in levels if lvl in SEVERITY_ORDER), key=SEVERITY_ORDER.__getitem__, default="clear")


class Finding:
    """One list or sanctions hit on one name."""

    def __init__(self, vendor_id: str, vendor_name: str, lists: list[str], status: str, flags: list[dict],
                 source: str, status_note: str = ""):
        self.vendor_id, self.vendor_name, self.lists = vendor_id, vendor_name, lists
        self.status, self.flags, self.source, self.status_note = status, flags, source, status_note

    @property
    def severity(self) -> str:
        return worst(*(f.get("severity", "watch") for f in self.flags)) if self.flags else "watch"

    def as_dict(self) -> dict:
        return {"vendor_id": self.vendor_id, "vendor": self.vendor_name, "lists": list(self.lists),
                "status": self.status, "status_note": self.status_note, "severity": self.severity,
                "flags": list(self.flags), "source": self.source}


class Screening:
    def __init__(self, name: str):
        self.name = name
        self.findings: list[Finding] = []
        self.organization: Optional[dict] = None
        self.offline = False
        self.errors: list[str] = []
        self.as_of = ""
        self.lists_pulled = ""

    @property
    def severity(self) -> str:
        return worst(*(f.severity for f in self.findings))

    @property
    def hit(self) -> bool:
        return bool(self.findings)

    def as_dict(self) -> dict:
        return {"name": self.name, "severity": self.severity, "hit": self.hit, "offline": self.offline,
                "findings": [f.as_dict() for f in self.findings], "organization": self.organization,
                "as_of": self.as_of, "lists_pulled": self.lists_pulled, "errors": list(self.errors)}


class Screener:
    def __init__(self, client: TensorustClient, index: ListIndex, lists: Optional[Iterable[str]] = None,
                 budget_seconds: float = 8.0):
        self.client = client
        self.index = index
        self.lists = [l for l in (lists or []) if isinstance(l, str)] or None
        self.budget = max(2.0, float(budget_seconds))
        self._refresh_lock = threading.Lock()
        self._refreshed_at: Optional[_dt.datetime] = None

    # -- list membership refresh (data only, in memory) ---------------------------------------------

    def refresh_lists(self, max_age_hours: float = 24.0) -> bool:
        """Pull fresh list membership from the API at most once per ``max_age_hours``."""
        now = _dt.datetime.now(_dt.timezone.utc)
        with self._refresh_lock:
            if self._refreshed_at and (now - self._refreshed_at).total_seconds() < max_age_hours * 3600:
                return False
            self._refreshed_at = now
        cards = {}
        for list_id in ("1260H", "FCC_COVERED", "SEC889", "UFLPA", "BIOSECURE"):
            try:
                cards[list_id] = self.client.call("exposure_summary", {"list": list_id})
            except TensorustUnavailable:
                return False
        self.index.replace_from_api(cards)
        return True

    # -- screening ----------------------------------------------------------------------------------

    def local_only(self, name: str) -> Screening:
        screening = Screening(name)
        screening.lists_pulled = self.index.pulled_at()
        for hit in self.index.match_name(name, self.lists):
            screening.findings.append(self._finding_from_local(hit))
        return screening

    def screen(self, name: str, *, description: str = "", amount: Optional[float] = None,
               date: str = "", with_organization: bool = True) -> Screening:
        screening = self.local_only(name)
        today = date or _dt.date.today().isoformat()
        line = {"vendor": name, "description": description or "", "date": today}
        if amount is not None:
            line["amount"] = amount
        jobs = {"purchases": _POOL.submit(self.client.call, "exposure_screen_purchases", {"lines": [line]})}
        if with_organization:
            jobs["organization"] = _POOL.submit(self.client.call, "lookup_organization", {"name": name})
        done, pending = concurrent.futures.wait(jobs.values(), timeout=self.budget)
        for future in pending:
            future.cancel()
        results: dict[str, Optional[dict]] = {}
        for key, future in jobs.items():
            if future in done:
                try:
                    results[key] = future.result()
                except TensorustUnavailable as exc:
                    results[key] = None
                    screening.errors.append(str(exc))
                except Exception as exc:  # defensive: never let a surprise wedge the hook
                    results[key] = None
                    screening.errors.append(f"{type(exc).__name__}: {exc}")
            else:
                results[key] = None
                screening.errors.append(f"{key}: no answer within {self.budget:.0f}s")
        purchases = results.get("purchases")
        organization = results.get("organization")
        if purchases is None and (not with_organization or organization is None):
            screening.offline = True
        if isinstance(purchases, dict):
            self._merge_purchase_screen(screening, purchases)
        if isinstance(organization, dict):
            self._merge_organization(screening, organization)
        return screening

    # -- merging ------------------------------------------------------------------------------------

    def _finding_from_local(self, hit: VendorHit) -> Finding:
        flags = []
        for list_id in hit.lists:
            rule = LIST_RULES.get(list_id, {})
            flags.append({"rule": rule.get("rule", list_id), "list": list_id,
                          "severity": "watch" if hit.status in ("contested", "enjoined") else "review",
                          "status": hit.status, "why": rule.get("why", ""), "citation": rule.get("citation", "")})
        return Finding(hit.vendor_id, hit.name, list(hit.lists), hit.status, flags, "bundled snapshot",
                       hit.status_note)

    def _merge_purchase_screen(self, screening: Screening, payload: dict) -> None:
        screening.as_of = str(payload.get("as_of") or screening.as_of)
        screening.lists_pulled = str(payload.get("lists_pulled") or screening.lists_pulled)
        by_vendor = payload.get("by_vendor") or {}
        items = payload.get("items") or []
        item = items[0] if items and isinstance(items[0], dict) else {}
        lists_at_date = [l for l in item.get("lists_at_date") or [] if self._list_allowed(l)]
        flags = []
        for flag in item.get("flags") or []:
            if not isinstance(flag, dict):
                continue
            flag = dict(flag)
            flag.setdefault("list", RULE_TO_LIST.get(str(flag.get("rule") or ""), ""))
            if flag["list"] and not self._list_allowed(flag["list"]):
                continue
            flags.append(flag)
        for vendor_id in item.get("vendors") or []:
            card = by_vendor.get(vendor_id) or {}
            lists = [l for l in card.get("lists") or lists_at_date if self._list_allowed(l)]
            if not lists:
                continue
            entry = self.index.vendor(vendor_id) or {}
            statuses = {entry.get("status", {}).get(l, "listed") for l in lists}
            status = "listed" if "listed" in statuses or not statuses else sorted(statuses)[0]
            api_finding = Finding(vendor_id, card.get("name") or entry.get("name") or vendor_id, lists, status,
                                  flags or [], "Tensorust API")
            if not api_finding.flags:
                api_finding = self._finding_from_local(
                    VendorHit(vendor_id, api_finding.vendor_name, lists, status, "", screening.name))
                api_finding.source = "Tensorust API"
            screening.findings = [f for f in screening.findings if f.vendor_id != vendor_id] + [api_finding]

    def _merge_organization(self, screening: Screening, card: dict) -> None:
        if not card.get("found"):
            screening.organization = {"found": False}
            return
        links = {}
        for link_name, link in (card.get("links") or {}).items():
            if isinstance(link, dict):
                links[link_name] = {"count": link.get("count"),
                                    "preview": [{"oid": p.get("oid"), "type": p.get("type"), "name": p.get("name")}
                                                for p in link.get("preview") or [] if isinstance(p, dict)]}
        props = card.get("props") or {}
        screening.organization = {
            "found": True, "oid": card.get("oid"), "legal_name": card.get("name"), "type": card.get("type"),
            "sources": [s for s in card.get("sources") or [] if s], "links": links,
            "obligations": [o for o in card.get("obligations") or [] if isinstance(o, dict)],
            "country": props.get("country"), "state": props.get("state"), "ticker": props.get("ticker"),
            "access": card.get("access"),
        }
        for obligation in screening.organization["obligations"]:
            if obligation.get("rule") == _OFAC_RULE and self._list_allowed("OFAC"):
                flag = {"rule": _OFAC_RULE, "list": "OFAC", "severity": "review",
                        "status": obligation.get("status", ""),
                        "why": "Name matches an OFAC sanctions list entry. Verify the match before any payment: "
                               + str(obligation.get("evidence") or ""),
                        "citation": "31 CFR chapter V (OFAC); Executive Order 13959 for CMIC entries"}
                finding = Finding("ofac:" + str(card.get("oid")), str(card.get("name")), ["OFAC"],
                                  obligation.get("status", "name_match_to_verify"), [flag], "Tensorust API")
                screening.findings.append(finding)

    def _list_allowed(self, list_id: str) -> bool:
        return self.lists is None or list_id in self.lists


# -- formatting ---------------------------------------------------------------------------------------

def format_findings(screenings: list[Screening], *, tool_name: str = "", header: str = "") -> str:
    """The text that goes into the approval prompt, the block message or the warn note."""
    lines: list[str] = []
    if header:
        lines.append(header)
    pulled = ""
    for screening in screenings:
        if not screening.hit:
            continue
        pulled = screening.lists_pulled or pulled
        for finding in screening.findings:
            label = ", ".join(list_label(l) for l in finding.lists)
            status = "" if finding.status == "listed" else f" ({finding.status})"
            lines.append(f"- {screening.name}: {finding.vendor_name}{status} is on {label}.")
            top = sorted(finding.flags, key=lambda f: SEVERITY_ORDER.get(f.get("severity", "watch"), 0),
                         reverse=True)[:2]
            for flag in top:
                why = (flag.get("why") or "").strip()
                cite = (flag.get("citation") or "").strip()
                if why:
                    lines.append(f"  {why}" + (f" [{cite}]" if cite else ""))
        if screening.offline:
            lines.append("  (Tensorust API unreachable: this comes from the bundled list snapshot.)")
    if pulled:
        lines.append(f"Source: Tensorust public-records ontology, lists pulled {pulled[:10]}.")
    return "\n".join(lines)
