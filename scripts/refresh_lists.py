"""Refresh data/lists.json from the Tensorust public-records API.

Maintainer script, run by hand before a release. It writes DATA only (the list
metadata and vendor membership the plugin ships as its offline fallback). The
plugin never runs this at install or runtime and never replaces its own code
(catalog rule 3). Usage:

    python scripts/refresh_lists.py

Network: one HTTPS POST per list to https://shop.tensorust.com/api/mcp.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MCP_URL = "https://shop.tensorust.com/api/mcp"
LISTS = ["1260H", "FCC_COVERED", "SEC889", "UFLPA", "BIOSECURE"]
OUT = Path(__file__).resolve().parents[1] / "data" / "lists.json"


def _call(tool: str, arguments: dict) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}}).encode("utf-8")
    req = urllib.request.Request(MCP_URL, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "User-Agent": "hermes-counterparty/refresh_lists"})
    raw = urllib.request.urlopen(req, timeout=40).read().decode("utf-8")
    text = raw.lstrip()
    if text.startswith("event") or text.startswith("data:"):
        raw = [line[5:] for line in raw.splitlines() if line.startswith("data:")][0]
    envelope = json.loads(raw)
    return json.loads(envelope["result"]["content"][0]["text"])


def main() -> int:
    snapshot = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": MCP_URL, "lists": {}}
    for list_id in LISTS:
        card = _call("exposure_summary", {"list": list_id})
        vendors = []
        for vendor in card.get("vendors") or []:
            vendors.append({k: vendor.get(k) for k in
                            ("id", "name", "entity", "status", "status_note", "first_listed", "effective")})
        snapshot["lists"][list_id] = {
            "name": card.get("name"), "operator": card.get("operator"), "cadence": card.get("cadence"),
            "source": card.get("source"), "entities": card.get("entities"),
            "fetched_at": card.get("fetched_at"), "latest_entry": card.get("latest_entry"),
            "vendors": vendors,
        }
        print(f"{list_id}: {len(vendors)} tracked vendor groups, {card.get('entities')} entities, "
              f"pulled {card.get('fetched_at')}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
