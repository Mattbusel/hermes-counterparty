"""counterparty: Hermes screens who your agent is about to pay, email or buy from.

Before an outward action (send an email, pay an invoice, place an order, sign,
submit a form) the ``pre_tool_call`` hook pulls the company, email domain or
URL out of the call's arguments and screens it against the DoD Section 1260H
list, the FCC Covered List, Section 889, the UFLPA Entity List, the BIOSECURE
list and OFAC sanctions, with public-record ownership from the Tensorust
ontology. On a hit it raises Hermes's own approval prompt with the finding and
its citation (``mode: ask``), refuses the call (``mode: block``) or lets it
through with a note the model sees (``mode: warn``). It never auto-approves,
and under cron or any other unattended run Hermes's gate fails closed.

``transform_tool_result`` appends a once-per-session note when a web search,
page or document mentions a listed vendor, so the agent learns about the
problem before it reaches the checkout.

Tools: cp_screen, cp_lookup, cp_credit, cp_who_owns_address, cp_brief.
Slash command: /whoowns <company | domain | street address, zip>.

Network: HTTPS to https://shop.tensorust.com/api/mcp only. No shell, no
background processes, no telemetry, no files outside the plugin's own data.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from . import schemas
from . import tools as _tools
from .client import TensorustClient
from .extract import extract_candidates, tool_matches
from .lists import LIST_IDS, ListIndex
from .screen import Screener, Screening, format_findings

logger = logging.getLogger(__name__)

PLUGIN_NAME = "counterparty"
SECRET_ENV = "TENSORUST_API_KEY"

DEFAULT_ACTION_TOOLS = [
    "send_email", "*send_mail*", "*_send", "*_send_*", "*_reply", "*email*", "*_pay", "*_pay_*", "*payment*",
    "*checkout*", "*purchase*", "*order*", "*invoice*", "*wire*", "*transfer*", "*_sign", "*sign_*",
    "*contract*", "*submit*", "*subscribe*", "*onboard*", "*vendor*", "*supplier*", "*procure*",
]
DEFAULT_WATCH_TOOLS = ["web_search", "web_extract", "x_search", "browser_*", "read_file", "*fetch*", "*scrape*"]
DEFAULT_LISTS = list(LIST_IDS) + ["OFAC"]

DEFAULTS: dict[str, Any] = {
    "mode": "ask",
    "action_tools": DEFAULT_ACTION_TOOLS,
    "watch_tools": DEFAULT_WATCH_TOOLS,
    "lists": DEFAULT_LISTS,
    "timeout_seconds": 6,
    "refresh_lists": True,
}
MODES = ("ask", "block", "warn", "off")

_SCAN_LIMIT = 200_000


class _Runtime:
    """Per-process plugin state: settings, the screener, session bookkeeping."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self._lock = threading.Lock()
        self._screener: Optional[Screener] = None
        self._refresh_started = False
        self.pending_notes: dict[str, str] = {}
        self.noted: dict[str, set[str]] = {}
        self.stats: dict[str, dict[str, int]] = {}
        self.last: dict[str, Screening] = {}

    # -- settings ----------------------------------------------------------------------------------

    def setting(self, key: str) -> Any:
        default = DEFAULTS[key]
        try:
            value = self.ctx.get_config(key, default=None)
        except Exception:
            value = None
        if value is None or value == "" or (isinstance(default, list) and not isinstance(value, list)):
            return default
        return value

    def mode(self) -> str:
        mode = str(self.setting("mode") or "ask").strip().lower()
        return mode if mode in MODES else "ask"

    def lists(self) -> list[str]:
        allowed = [str(l).upper() for l in self.setting("lists") if isinstance(l, str)]
        return allowed or DEFAULT_LISTS

    def timeout(self) -> float:
        try:
            return max(1.0, min(30.0, float(self.setting("timeout_seconds"))))
        except (TypeError, ValueError):
            return 6.0

    # -- screener ----------------------------------------------------------------------------------

    def screener(self) -> Screener:
        with self._lock:
            if self._screener is None:
                client = TensorustClient(timeout=self.timeout(), api_key_getter=_api_key)
                self._screener = Screener(client, ListIndex.bundled(), lists=self.lists(),
                                          budget_seconds=self.timeout() + 2.0)
            screener = self._screener
            screener.lists = self.lists()
            start_refresh = bool(self.setting("refresh_lists")) and not self._refresh_started
            self._refresh_started = True
        if start_refresh:
            threading.Thread(target=self._refresh, args=(screener,), name="counterparty-lists", daemon=True).start()
        return screener

    @staticmethod
    def _refresh(screener: Screener) -> None:
        try:
            if screener.refresh_lists():
                logger.debug("counterparty: list membership refreshed from the Tensorust API")
        except Exception as exc:  # the bundled snapshot stays in place
            logger.debug("counterparty: list refresh skipped: %s", exc)

    # -- bookkeeping -------------------------------------------------------------------------------

    def bump(self, session_id: str, key: str) -> None:
        with self._lock:
            self.stats.setdefault(session_id or "-", {}).setdefault(key, 0)
            self.stats[session_id or "-"][key] += 1

    def note_once(self, session_id: str, vendor_id: str) -> bool:
        with self._lock:
            seen = self.noted.setdefault(session_id or "-", set())
            if vendor_id in seen:
                return False
            seen.add(vendor_id)
            return True


_runtime: Optional[_Runtime] = None


def _api_key() -> Optional[str]:
    """The optional Tensorust key, read only through Hermes's secret scope (catalog rule 11)."""
    try:
        from agent.secret_scope import get_secret
    except Exception:
        return None
    try:
        return get_secret(SECRET_ENV)
    except Exception:
        return None


# -- hooks ------------------------------------------------------------------------------------------

def _screen_call(rt: _Runtime, tool_name: str, args: Any) -> list[Screening]:
    candidates, text = extract_candidates(args)
    screener = rt.screener()
    screenings: list[Screening] = []
    started = time.monotonic()
    for candidate in candidates[:3]:
        if time.monotonic() - started > screener.budget * 2:
            break
        screenings.append(screener.screen(candidate.value))
    covered = {s.name.lower() for s in screenings}
    for hit in screener.index.match_text(text[:_SCAN_LIMIT], screener.lists):
        if any(f.vendor_id == hit.vendor_id for s in screenings for f in s.findings):
            continue
        if hit.alias.lower() in covered:
            continue
        screening = screener.local_only(hit.alias)
        screening.name = f"text mentions {hit.alias}"
        screenings.append(screening)
    return screenings


def _on_pre_tool_call(tool_name: str = "", args: Any = None, tool_call_id: str = "", session_id: str = "",
                      task_id: str = "", **_: Any) -> Optional[dict]:
    rt = _runtime
    if rt is None:
        return None
    try:
        mode = rt.mode()
        if mode == "off" or not tool_matches(tool_name, rt.setting("action_tools")):
            return None
        if not isinstance(args, dict) or not args:
            return None
        session = session_id or task_id
        screenings = _screen_call(rt, tool_name, args)
        if not screenings:
            return None
        rt.bump(session, "screened")
        for screening in screenings:
            rt.last[session or "-"] = screening
        hits = [s for s in screenings if s.hit]
        if not hits:
            if any(s.offline for s in screenings) and mode == "warn":
                rt.pending_notes[tool_call_id or ""] = (
                    "counterparty: the Tensorust API was unreachable, so only the bundled list snapshot was checked.")
            return None
        rt.bump(session, "flagged")
        vendor_ids = sorted({f.vendor_id for s in hits for f in s.findings})
        header = (f"counterparty screen for {tool_name}: "
                  f"{len(hits)} flagged counterpart{'y' if len(hits) == 1 else 'ies'}")
        message = format_findings(hits, tool_name=tool_name, header=header)
        if mode == "block":
            return {"action": "block", "message": message + "\nBlocked by the counterparty plugin (mode: block). "
                    "Tell the user what was found and ask how to proceed."}
        if mode == "warn":
            rt.pending_notes[tool_call_id or ""] = message
            return None
        return {"action": "approve", "message": message,
                "rule_key": f"counterparty:{tool_name}:{','.join(vendor_ids)}"}
    except Exception as exc:
        logger.warning("counterparty: pre_tool_call skipped after error: %s", exc)
        return None


def _on_transform_tool_result(tool_name: str = "", args: Any = None, result: Any = None, tool_call_id: str = "",
                              session_id: str = "", task_id: str = "", **_: Any) -> Optional[str]:
    rt = _runtime
    if rt is None or not isinstance(result, str):
        return None
    try:
        additions: list[str] = []
        pending = rt.pending_notes.pop(tool_call_id or "", None)
        if pending:
            additions.append(pending)
        if rt.mode() != "off" and tool_matches(tool_name, rt.setting("watch_tools")) and result:
            screener = rt.screener()
            session = session_id or task_id
            fresh = [hit for hit in screener.index.match_text(result[:_SCAN_LIMIT], screener.lists)
                     if rt.note_once(session, hit.vendor_id)]
            if fresh:
                rt.bump(session, "noted")
                lines = ["counterparty: this result mentions listed vendors."]
                for hit in fresh:
                    screening = screener.local_only(hit.alias)
                    lines.append(format_findings([screening]).split("\nSource:")[0])
                lines.append(f"Lists pulled {screener.index.pulled_at()[:10]}. Run cp_screen or cp_brief before "
                             "buying from, paying or contracting with any of them.")
                additions.append("\n".join(lines))
        if not additions:
            return None
        return result + "\n\n---\n" + "\n\n".join(additions)
    except Exception as exc:
        logger.debug("counterparty: transform_tool_result skipped after error: %s", exc)
        return None


# -- slash command ----------------------------------------------------------------------------------

def _whoowns(raw_args: str = "", *, session_id: str = "", **_: Any) -> str:
    rt = _runtime
    if rt is None:
        return "counterparty is not loaded."
    query = (raw_args or "").strip()
    if not query:
        stats = rt.stats.get(session_id or "-", {})
        return ("Usage: /whoowns <company, brand or domain>  or  /whoowns <street address>, <zip>\n"
                f"This session: {stats.get('screened', 0)} outward calls screened, {stats.get('flagged', 0)} flagged, "
                f"{stats.get('noted', 0)} listed vendors noted in results. Mode: {rt.mode()}.")
    tctx = _tools.ToolContext(rt.screener())
    address = _tools.looks_like_address(query)
    if address:
        payload = json.loads(_tools.cp_who_owns_address(tctx, {"address": address[0], "zip": address[1]}))
        return "```json\n" + json.dumps(payload, indent=1, ensure_ascii=False) + "\n```"
    markdown, _severity = _tools.build_brief(tctx, query)
    return markdown


# -- registration -----------------------------------------------------------------------------------

def register(ctx: Any) -> None:
    global _runtime
    _runtime = _Runtime(ctx)
    tctx_holder: dict[str, _tools.ToolContext] = {}

    def tool_ctx() -> _tools.ToolContext:
        if "ctx" not in tctx_holder:
            tctx_holder["ctx"] = _tools.ToolContext(_runtime.screener())
        return tctx_holder["ctx"]

    def bind(handler):
        def run(args: dict, **kwargs: Any) -> str:
            try:
                return handler(tool_ctx(), args if isinstance(args, dict) else {}, **kwargs)
            except Exception as exc:
                logger.warning("counterparty: %s failed: %s", handler.__name__, exc)
                return json.dumps({"error": f"{handler.__name__} failed: {type(exc).__name__}: {exc}"})
        run.__name__ = handler.__name__
        return run

    for schema, handler in ((schemas.CP_SCREEN, _tools.cp_screen), (schemas.CP_LOOKUP, _tools.cp_lookup),
                            (schemas.CP_CREDIT, _tools.cp_credit),
                            (schemas.CP_WHO_OWNS_ADDRESS, _tools.cp_who_owns_address),
                            (schemas.CP_BRIEF, _tools.cp_brief)):
        ctx.register_tool(name=schema["name"], toolset=PLUGIN_NAME, schema=schema, handler=bind(handler),
                          description=schema["description"][:120], emoji="🛃")

    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    ctx.register_command("whoowns", handler=_whoowns, args_hint="<company or address, zip>",
                         description="Cited brief on who owns a company and which federal lists it is on")
