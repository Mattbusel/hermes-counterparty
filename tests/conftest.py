"""Load the plugin the way Hermes does (a package under ``hermes_plugins``) and offer fakes.

No Hermes install is needed for these tests: the plugin imports Hermes only lazily, for the secret
scope, and the fakes below stand in for the plugin context and for the Tensorust API.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
MODULE_NAME = "hermes_plugins.counterparty"


def _load_plugin():
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []  # type: ignore[attr-defined]
        sys.modules["hermes_plugins"] = namespace
    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_DIR / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def plugin():
    return _load_plugin()


@pytest.fixture(scope="session")
def submodules(plugin):
    import importlib
    return {name: importlib.import_module(f"{MODULE_NAME}.{name}")
            for name in ("client", "lists", "extract", "screen", "tools", "schemas")}


class FakeCtx:
    """The slice of PluginContext the plugin uses."""

    def __init__(self, settings: dict | None = None):
        self.settings = dict(settings or {})
        self.tools: dict = {}
        self.hooks: dict = {}
        self.commands: dict = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kwargs}

    def register_hook(self, name, callback, **kwargs):
        self.hooks[name] = callback

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = {"handler": handler, **kwargs}


@pytest.fixture
def fake_ctx():
    return FakeCtx


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def canned_api(responses: dict, *, calls: list | None = None):
    """An ``urlopen`` stand-in: ``responses`` maps tool name -> result dict (or an Exception to raise)."""

    def opener(request, timeout=None):
        payload = json.loads(request.data.decode("utf-8"))
        tool = payload["params"]["name"]
        if calls is not None:
            calls.append((tool, payload["params"]["arguments"], dict(request.headers)))
        answer = responses.get(tool)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(payload["params"]["arguments"])
        if answer is None:
            answer = {"found": False}
        envelope = {"jsonrpc": "2.0", "id": payload["id"],
                    "result": {"content": [{"type": "text", "text": json.dumps(answer)}]}}
        return _Response(json.dumps(envelope).encode("utf-8"))

    return opener


HIKVISION_PURCHASE = {
    "as_of": "2026-11-06", "lists_pulled": "2026-11-06T08:58:16Z", "lines": 1, "matched_lines": 1,
    "by_vendor": {"hikvision": {"name": "Hikvision (video surveillance)", "lines": 1,
                                "lists": ["1260H", "FCC_COVERED", "SEC889"]}},
    "items": [{"row": 1, "vendor": "Hikvision USA Inc", "vendors": ["hikvision"],
               "lists_at_date": ["1260H", "FCC_COVERED", "SEC889"],
               "flags": [{"rule": "889-GRANTS", "severity": "review", "status": "check_funding_source",
                          "why": "Hikvision is covered telecom or video surveillance under Section 889.",
                          "citation": "Pub. L. 115-232 sec. 889(b)(1); 2 CFR 200.216; FAR 52.204-25"},
                         {"rule": "FCC-COVERED", "severity": "watch", "status": "informational",
                          "why": "On the FCC Covered List.", "citation": "47 U.S.C. 1601-1609"}],
               "worst": "review"}],
}
CLEAN_PURCHASE = {"as_of": "2026-11-06", "lists_pulled": "2026-11-06T08:58:16Z", "lines": 1, "matched_lines": 0,
                  "by_vendor": {}, "items": [{"row": 1, "vendor": "Dell Technologies", "vendors": [],
                                              "lists_at_date": [], "flags": [], "worst": "clear"}]}
HIKVISION_ORG = {
    "found": True, "oid": "org:HANGZHOU HIKVISION DIGITAL TECHNOLOGY",
    "name": "Hangzhou Hikvision Digital Technology Co., Ltd.", "type": "Organization", "sources": [""],
    "links": {"SANCTIONED": {"count": 1, "preview": [{"oid": "sanction:30946", "type": "Sanction",
                                                       "name": "HANGZHOU HIKVISION DIGITAL TECHNOLOGY CO., LTD."}]}},
    "obligations": [{"rule": "OFAC-SDN", "name": "OFAC sanctions list match", "status": "name_match_to_verify",
                     "evidence": "Consolidated (non-SDN) | CMIC-EO13959"}],
    "props": {}, "access": "preview",
}
DELL_ORG = {"found": True, "oid": "org:DELL TECHNOLOGIES", "name": "DELL TECHNOLOGIES INC", "type": "Organization",
            "sources": ["sec_frames", "dol_5500"], "links": {}, "obligations": [], "props": {"ticker": "DELL"}}
