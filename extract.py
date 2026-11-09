"""Pull counterparty candidates out of a tool call's arguments.

Deliberately conservative: a company name is taken only from fields that name a
party (vendor, payee, recipient, merchant, ...), from the domain of an email
address or URL, and otherwise from free text only for the alias scan. The
decision of whether a name is actually on a list is made elsewhere; this
module only decides what is worth asking about.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any, Iterable
from urllib.parse import urlparse

NAME_FIELDS = {
    "vendor", "vendor_name", "supplier", "supplier_name", "company", "company_name", "counterparty",
    "counterparty_name", "payee", "payee_name", "merchant", "merchant_name", "recipient_name", "to_name",
    "seller", "seller_name", "beneficiary", "beneficiary_name", "business", "business_name", "organization",
    "organisation", "org", "org_name", "account_name", "account_holder", "contractor", "manufacturer", "brand",
    "customer", "customer_name", "client", "client_name", "partner", "entity", "entity_name", "legal_name",
    "name", "display_name", "label",
}
EMAIL_FIELDS = {
    "to", "cc", "bcc", "email", "emails", "recipient", "recipients", "to_email", "to_emails", "recipient_email",
    "from", "reply_to", "address", "addresses", "contact", "contact_email",
}
URL_FIELDS = {"url", "href", "link", "website", "domain", "homepage", "endpoint", "site", "store", "shop"}
TEXT_FIELDS = {
    "subject", "body", "message", "text", "content", "html", "description", "memo", "note", "notes", "reason",
    "purpose", "summary", "title", "comment", "details", "items", "line_items", "lines", "query", "prompt",
}

FREE_MAIL = {
    "gmail", "googlemail", "outlook", "hotmail", "live", "msn", "yahoo", "ymail", "icloud", "me", "mac", "aol",
    "proton", "protonmail", "pm", "fastmail", "hey", "gmx", "mail", "zoho", "yandex", "qq", "163", "126",
}
_PUBLIC_SUFFIX_2 = {"co", "com", "org", "net", "gov", "edu", "ac", "ltd", "plc"}
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_MAX_DEPTH = 3
_MAX_CANDIDATES = 6
_MAX_TEXT = 200_000


class Candidate:
    __slots__ = ("value", "kind", "field")

    def __init__(self, value: str, kind: str, field: str):
        self.value, self.kind, self.field = value, kind, field

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Candidate({self.value!r}, {self.kind}, {self.field})"

    def as_dict(self) -> dict:
        return {"value": self.value, "kind": self.kind, "field": self.field}


def tool_matches(tool_name: str, patterns: Iterable[str]) -> bool:
    """fnmatch-style match of a tool name against the configured action or watch patterns."""
    name = (tool_name or "").lower()
    for pattern in patterns or ():
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        if fnmatch.fnmatchcase(name, pattern.strip().lower()):
            return True
    return False


def domain_to_name(domain: str) -> str | None:
    """``orders.hikvision.com`` -> ``hikvision``; free-mail and bare TLDs give None."""
    host = (domain or "").strip().lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = [p for p in host.split(".") if p]
    if len(labels) < 2:
        return None
    # second-level public suffixes such as co.uk / com.au
    if len(labels) >= 3 and labels[-2] in _PUBLIC_SUFFIX_2 and len(labels[-1]) == 2:
        label = labels[-3]
    else:
        label = labels[-2]
    if label in FREE_MAIL or len(label) < 3:
        return None
    return label


def extract_candidates(args: Any) -> tuple[list[Candidate], str]:
    """Return ``(structured candidates, free text)`` for a tool call's arguments.

    Structured candidates are names, email domains and URL hosts worth a records lookup.
    The free text is everything string-valued, joined, for the local alias scan only."""
    names: list[Candidate] = []
    texts: list[str] = []
    seen: set[str] = set()

    def add(value: str, kind: str, field: str) -> None:
        key = kind + ":" + value.lower()
        if key in seen or len(names) >= _MAX_CANDIDATES:
            return
        seen.add(key)
        names.append(Candidate(value, kind, field))

    def visit(node: Any, field: str, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                visit(value, str(key).lower(), depth + 1)
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                visit(item, field, depth + 1)
            return
        if not isinstance(node, str):
            return
        value = node.strip()
        if not value:
            return
        texts.append(value[:_MAX_TEXT])
        base = field.rsplit(".", 1)[-1]
        if base in EMAIL_FIELDS or "@" in value and len(value) < 200:
            for match in _EMAIL_RE.finditer(value):
                label = domain_to_name(match.group(1))
                if label:
                    add(label, "email_domain", field)
            if base in EMAIL_FIELDS:
                return
        if base in URL_FIELDS or value.startswith(("http://", "https://")):
            host = urlparse(value if "://" in value else "https://" + value).hostname or ""
            label = domain_to_name(host)
            if label:
                add(label, "url_host", field)
            return
        if base in NAME_FIELDS and 3 <= len(value) <= 120 and "\n" not in value:
            add(value, "name", field)

    visit(args, "", 0)
    return names, "\n".join(texts)
