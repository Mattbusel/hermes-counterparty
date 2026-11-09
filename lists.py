"""The banned-vendor lists the plugin screens against, with an offline snapshot.

``data/lists.json`` is a snapshot of the five federal lists as the Tensorust
API tracks them (which vendor groups each list covers, when it was pulled, the
operating agency). It is data, refreshed by a maintainer with
``scripts/refresh_lists.py`` and reviewed in a normal commit. At runtime the
index can also take a fresher membership pulled from the API, in memory only.

``ALIASES`` maps each vendor group to the names it trades under. Every alias is
taken from the list entries themselves (legal names, subsidiaries named in the
notice, instrument lines) or is a plain spelling variant. Short tokens such as
``DJI`` or ``ZTE`` match on word boundaries only.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Iterable, Optional

DATA_PATH = Path(__file__).resolve().parent / "data" / "lists.json"

LIST_IDS = ("1260H", "FCC_COVERED", "SEC889", "UFLPA", "BIOSECURE")

# Plain-language label, the rule it feeds, and the citation shown when the API is unreachable.
LIST_RULES: dict[str, dict[str, str]] = {
    "1260H": {
        "label": "DoD Section 1260H Chinese military companies list",
        "rule": "1260H",
        "citation": "FY2021 NDAA sec. 1260H; Federal Register notices by the Department of Defense",
        "why": "Named by the Department of Defense as a Chinese military company. Procurement bans attach to the list under later NDAAs and agency rules.",
    },
    "FCC_COVERED": {
        "label": "FCC Covered List",
        "rule": "FCC-COVERED",
        "citation": "47 U.S.C. 1601-1609; 47 CFR 1.50002, 54.9",
        "why": "On the FCC Covered List: no new FCC equipment authorizations, and no FCC-administered subsidy money (E-Rate, Rural Health Care, USF) may buy it.",
    },
    "SEC889": {
        "label": "Section 889 covered telecom and video surveillance",
        "rule": "889-GRANTS",
        "citation": "Pub. L. 115-232 sec. 889; FAR 52.204-25; 2 CFR 200.216",
        "why": "Covered telecom or video surveillance under Section 889. Federal grant, cooperative agreement and loan funds may not buy it, and federal contractors must report it.",
    },
    "UFLPA": {
        "label": "UFLPA Entity List",
        "rule": "UFLPA-IMPORT",
        "citation": "Pub. L. 117-78; 19 U.S.C. 1307",
        "why": "On the UFLPA Entity List: new US imports of its goods are presumed made with forced labor and can be detained at the border.",
    },
    "BIOSECURE": {
        "label": "BIOSECURE biotechnology companies of concern",
        "rule": "BIOSECURE-851",
        "citation": "FY2026 NDAA (Pub. L. 119-60) sec. 851",
        "why": "A biotechnology company of concern under the BIOSECURE Act. Federal awards may not buy its equipment or services once the ban takes effect.",
    },
}

ALIASES: dict[str, tuple[str, ...]] = {
    "autel": ("Autel Robotics", "Autel Intelligent Technology", "Autel Technology", "Autel"),
    "bgi": ("BGI Group", "BGI Genomics", "BGI Americas", "Complete Genomics", "Forensic Genomics International",
            "Innomics", "STomics", "GBI Diagnostics", "BGI"),
    "china_telecom_carriers": ("China Mobile", "China Telecom", "China Unicom", "Aspire Technologies"),
    "dahua": ("Zhejiang Dahua", "Dahua Technology", "Dahua"),
    "dji": ("Shenzhen DJI", "Da-Jiang Innovations", "Dajiang Baiwang", "DJI"),
    "hesai": ("Hesai Group", "Hesai Technology", "Hesai"),
    "hikvision": ("Hangzhou Hikvision Digital Technology", "Hangzhou Hikvision", "Hikvision"),
    "huawei": ("Huawei Investment & Holding", "Huawei Investment and Holding", "Huawei Technologies",
               "Huawei Holding", "Huawei"),
    "hytera": ("Hytera Communications", "Hytera"),
    "kaspersky": ("Kaspersky Lab", "AO Kaspersky", "Kaspersky"),
    "mgi": ("MGI Tech", "DNBSEQ", "BGISEQ", "MGISEQ", "MGI"),
    "novogene": ("Novogene",),
    "quectel": ("Quectel Wireless", "Quectel"),
    "robosense": ("RoboSense Technology", "RoboSense"),
    "tp_link": ("TP-Link Technologies", "TP-Link Systems", "TP-Link", "TPLink", "TP Link"),
    "unitree": ("Hangzhou Yushu Technology", "Yushu Technology", "Unitree"),
    "wuxi_apptec": ("WuXi AppTec", "Wuxi Apptec"),
    "zte": ("ZTE Corporation", "Zhongxing Telecommunications", "ZTE"),
}


class VendorHit:
    __slots__ = ("vendor_id", "name", "lists", "status", "status_note", "alias")

    def __init__(self, vendor_id: str, name: str, lists: list[str], status: str, status_note: str, alias: str):
        self.vendor_id, self.name, self.lists = vendor_id, name, lists
        self.status, self.status_note, self.alias = status, status_note, alias

    def as_dict(self) -> dict:
        return {"vendor_id": self.vendor_id, "name": self.name, "lists": list(self.lists), "status": self.status,
                "status_note": self.status_note, "matched_alias": self.alias}


class ListIndex:
    """Vendor-group membership across the five lists, plus alias matching on free text."""

    def __init__(self, snapshot: dict):
        self._lock = threading.Lock()
        self._load(snapshot)

    # -- construction -------------------------------------------------------------------------------

    @classmethod
    def bundled(cls) -> "ListIndex":
        with open(DATA_PATH, "r", encoding="utf-8") as handle:
            return cls(json.load(handle))

    def _load(self, snapshot: dict) -> None:
        lists = snapshot.get("lists") or {}
        vendors: dict[str, dict] = {}
        meta: dict[str, dict] = {}
        for list_id, card in lists.items():
            if not isinstance(card, dict):
                continue
            meta[list_id] = {k: card.get(k) for k in
                             ("name", "operator", "cadence", "source", "entities", "fetched_at", "latest_entry")}
            for vendor in card.get("vendors") or []:
                vid = str(vendor.get("id") or "").strip()
                if not vid:
                    continue
                entry = vendors.setdefault(vid, {"id": vid, "name": vendor.get("name") or vid, "lists": [],
                                                 "status": {}, "status_note": {}, "entity": {}})
                if list_id not in entry["lists"]:
                    entry["lists"].append(list_id)
                entry["status"][list_id] = vendor.get("status") or "listed"
                entry["status_note"][list_id] = vendor.get("status_note") or ""
                entry["entity"][list_id] = vendor.get("entity") or ""
        patterns: list[tuple[re.Pattern, str, str]] = []
        for vid, aliases in ALIASES.items():
            if vid not in vendors:
                continue
            for alias in sorted(aliases, key=len, reverse=True):
                escaped = re.escape(alias).replace(r"\ ", r"[\s\-]*").replace(r"\-", r"[\s\-]*")
                patterns.append((re.compile(r"(?<![A-Za-z0-9])" + escaped + r"(?![A-Za-z0-9])", re.IGNORECASE),
                                 vid, alias))
        with self._lock:
            self._vendors = vendors
            self._meta = meta
            self._patterns = patterns
            self.generated_at = snapshot.get("generated_at") or ""
            self.source = snapshot.get("source") or ""

    def replace_from_api(self, cards_by_list: dict[str, dict]) -> None:
        """Swap in list membership freshly pulled from the API (in memory only)."""
        snapshot = {"generated_at": "", "source": "api", "lists": {}}
        for list_id, card in cards_by_list.items():
            if isinstance(card, dict) and isinstance(card.get("vendors"), list):
                snapshot["lists"][list_id] = card
        if snapshot["lists"]:
            self._load(snapshot)

    # -- queries ------------------------------------------------------------------------------------

    @property
    def vendor_ids(self) -> list[str]:
        return sorted(self._vendors)

    def vendor(self, vendor_id: str) -> Optional[dict]:
        return self._vendors.get(vendor_id)

    def list_meta(self, list_id: str) -> dict:
        return dict(self._meta.get(list_id) or {})

    def pulled_at(self) -> str:
        stamps = [m.get("fetched_at") for m in self._meta.values() if m.get("fetched_at")]
        return max(stamps) if stamps else self.generated_at

    def match_text(self, text: str, only_lists: Optional[Iterable[str]] = None) -> list[VendorHit]:
        """Every vendor group whose alias appears in ``text``, one hit per group."""
        if not text:
            return []
        allowed = set(only_lists) if only_lists else None
        hits: dict[str, VendorHit] = {}
        for pattern, vid, alias in self._patterns:
            if vid in hits or not pattern.search(text):
                continue
            entry = self._vendors[vid]
            lists = [lid for lid in entry["lists"] if allowed is None or lid in allowed]
            if not lists:
                continue
            statuses = {entry["status"].get(lid, "listed") for lid in lists}
            status = "listed" if "listed" in statuses else sorted(statuses)[0]
            notes = [entry["status_note"].get(lid, "") for lid in lists if entry["status_note"].get(lid)]
            hits[vid] = VendorHit(vid, entry["name"], lists, status, notes[0] if notes else "", alias)
        return list(hits.values())

    def match_name(self, name: str, only_lists: Optional[Iterable[str]] = None) -> list[VendorHit]:
        return self.match_text(name or "", only_lists)
