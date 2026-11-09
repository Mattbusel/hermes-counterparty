"""The plugin surface as Hermes sees it: registration, hook decisions, settings, command."""

import json
import urllib.error
from pathlib import Path

import pytest

from conftest import CLEAN_PURCHASE, DELL_ORG, HIKVISION_ORG, HIKVISION_PURCHASE, PLUGIN_DIR, canned_api

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def _register(plugin, submodules, fake_ctx, settings=None, responses=None):
    ctx = fake_ctx(settings)
    plugin.register(ctx)
    responses = responses if responses is not None else {"exposure_screen_purchases": HIKVISION_PURCHASE,
                                                         "lookup_organization": HIKVISION_ORG}
    rt = plugin._runtime
    rt._refresh_started = True  # no background refresh in tests
    client = submodules["client"].TensorustClient(timeout=2, opener=canned_api(responses))
    rt._screener = submodules["screen"].Screener(client, submodules["lists"].ListIndex.bundled(),
                                                 lists=rt.lists(), budget_seconds=3)
    return ctx


def test_registers_declared_surface(plugin, submodules, fake_ctx):
    ctx = _register(plugin, submodules, fake_ctx)
    assert set(ctx.tools) == {"cp_screen", "cp_lookup", "cp_credit", "cp_who_owns_address", "cp_brief"}
    assert set(ctx.hooks) == {"pre_tool_call", "transform_tool_result"}
    assert "whoowns" in ctx.commands
    assert all(entry["toolset"] == "counterparty" for entry in ctx.tools.values())


def test_manifest_matches_registrations_and_defaults(plugin, submodules, fake_ctx):
    if yaml is None:
        pytest.skip("pyyaml not installed")
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))
    ctx = _register(plugin, submodules, fake_ctx)
    assert set(manifest["provides_tools"]) == set(ctx.tools)
    assert set(manifest["provides_hooks"]) == set(ctx.hooks)
    schema = manifest["config_schema"]
    for key, default in plugin.DEFAULTS.items():
        assert key in schema, key
        assert schema[key].get("default") == default, key
    assert schema["tensorust_api_key"]["type"] == "secret"
    assert schema["tensorust_api_key"]["env"] == plugin.SECRET_ENV
    assert manifest["requires_hermes"] == ">=0.23.0"


def test_ask_mode_escalates_listed_vendor(plugin, submodules, fake_ctx):
    ctx = _register(plugin, submodules, fake_ctx)
    directive = ctx.hooks["pre_tool_call"](tool_name="send_email", args={"to": "orders@hikvision.com",
                                                                        "subject": "PO", "body": "x"},
                                           tool_call_id="c1", session_id="s1")
    assert directive["action"] == "approve"
    assert directive["rule_key"] == "counterparty:send_email:hikvision,ofac:org:HANGZHOU HIKVISION DIGITAL TECHNOLOGY"
    assert "Section 889" in directive["message"] and "FAR 52.204-25" in directive["message"]


def test_block_mode_blocks_with_message(plugin, submodules, fake_ctx):
    ctx = _register(plugin, submodules, fake_ctx, settings={"mode": "block"})
    directive = ctx.hooks["pre_tool_call"](tool_name="laso_send_payment",
                                           args={"recipient_name": "Hikvision USA Inc", "amount": 48000},
                                           tool_call_id="c1", session_id="s1")
    assert directive["action"] == "block"
    assert "mode: block" in directive["message"]


def test_warn_mode_lets_call_through_and_annotates_result(plugin, submodules, fake_ctx):
    ctx = _register(plugin, submodules, fake_ctx, settings={"mode": "warn"})
    args = {"to": "orders@hikvision.com", "subject": "PO", "body": "x"}
    assert ctx.hooks["pre_tool_call"](tool_name="send_email", args=args, tool_call_id="c9", session_id="s1") is None
    out = ctx.hooks["transform_tool_result"](tool_name="send_email", args=args, result='{"ok": true}',
                                             tool_call_id="c9", session_id="s1")
    assert out.startswith('{"ok": true}') and "Hikvision" in out
    # the note is consumed once
    assert ctx.hooks["transform_tool_result"](tool_name="send_email", args=args, result='{"ok": true}',
                                              tool_call_id="c9", session_id="s1") is None


def test_off_mode_disables_hooks_but_keeps_tools(plugin, submodules, fake_ctx):
    ctx = _register(plugin, submodules, fake_ctx, settings={"mode": "off"})
    assert ctx.hooks["pre_tool_call"](tool_name="send_email", args={"to": "orders@hikvision.com"}) is None
    assert ctx.hooks["transform_tool_result"](tool_name="web_search", args={}, result="Hikvision") is None
    assert json.loads(ctx.tools["cp_screen"]["handler"]({"name": "Hikvision"}))["hit"] is True


def test_clean_counterparty_and_unwatched_tools_pass_silently(plugin, submodules, fake_ctx):
    ctx = _register(plugin, submodules, fake_ctx, responses={"exposure_screen_purchases": CLEAN_PURCHASE,
                                                             "lookup_organization": DELL_ORG})
    hook = ctx.hooks["pre_tool_call"]
    assert hook(tool_name="send_email", args={"to": "ap@dell.com", "subject": "PO"}, tool_call_id="a") is None
    assert hook(tool_name="terminal", args={"command": "ls"}, tool_call_id="b") is None
    assert hook(tool_name="write_file", args={"path": "x", "content": "Hikvision"}, tool_call_id="c") is None
    assert hook(tool_name="send_email", args={}, tool_call_id="d") is None


def test_offline_hit_still_asks_offline_clean_lets_through(plugin, submodules, fake_ctx):
    boom = urllib.error.URLError("down")
    ctx = _register(plugin, submodules, fake_ctx, responses={"exposure_screen_purchases": boom,
                                                             "lookup_organization": boom})
    hook = ctx.hooks["pre_tool_call"]
    directive = hook(tool_name="send_email", args={"to": "sales@dahua.com"}, tool_call_id="a", session_id="s")
    assert directive["action"] == "approve" and "bundled list snapshot" in directive["message"]
    assert hook(tool_name="send_email", args={"to": "ap@dell.com"}, tool_call_id="b", session_id="s") is None


def test_free_text_mention_in_outward_call_is_caught_locally(plugin, submodules, fake_ctx):
    ctx = _register(plugin, submodules, fake_ctx, responses={"exposure_screen_purchases": CLEAN_PURCHASE,
                                                             "lookup_organization": DELL_ORG})
    directive = ctx.hooks["pre_tool_call"](tool_name="send_email",
                                           args={"to": "ap@dell.com", "subject": "PO",
                                                 "body": "Please ship the Hytera radios"},
                                           tool_call_id="a", session_id="s")
    assert directive["action"] == "approve" and "Hytera" in directive["message"]


def test_search_results_are_annotated_once_per_session(plugin, submodules, fake_ctx):
    ctx = _register(plugin, submodules, fake_ctx)
    transform = ctx.hooks["transform_tool_result"]
    first = transform(tool_name="web_search", args={}, result="Hikvision and Dahua lead the budget tier",
                      tool_call_id="x", session_id="s2")
    assert "listed vendors" in first and "Dahua" in first and "Hikvision" in first
    assert transform(tool_name="web_search", args={}, result="Hikvision again", tool_call_id="y",
                     session_id="s2") is None
    assert "Hikvision" in transform(tool_name="web_search", args={}, result="Hikvision", tool_call_id="z",
                                    session_id="other")
    assert transform(tool_name="terminal", args={}, result="Hikvision", tool_call_id="w", session_id="s3") is None


def test_hook_survives_internal_errors(plugin, submodules, fake_ctx, monkeypatch):
    ctx = _register(plugin, submodules, fake_ctx)

    def explode(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin, "_screen_call", explode)
    assert ctx.hooks["pre_tool_call"](tool_name="send_email", args={"to": "x@hikvision.com"}, tool_call_id="a") is None


def test_settings_fall_back_to_defaults_on_bad_values(plugin, submodules, fake_ctx):
    _register(plugin, submodules, fake_ctx, settings={"mode": "sideways", "action_tools": "not-a-list",
                                                      "timeout_seconds": "fast", "lists": []})
    rt = plugin._runtime
    assert rt.mode() == "ask"
    assert rt.setting("action_tools") == plugin.DEFAULT_ACTION_TOOLS
    assert rt.timeout() == 6.0
    assert rt.lists() == plugin.DEFAULT_LISTS


def test_whoowns_command_usage_and_brief(plugin, submodules, fake_ctx):
    ctx = _register(plugin, submodules, fake_ctx)
    handler = ctx.commands["whoowns"]["handler"]
    usage = handler("", session_id="s1")
    assert usage.startswith("Usage: /whoowns")
    brief = handler("Hikvision", session_id="s1")
    assert brief.startswith("# Counterparty brief: Hikvision")
    assert "Verdict: REVIEW" in brief and "## Sources" in brief


def test_tool_handlers_validate_input(plugin, submodules, fake_ctx):
    ctx = _register(plugin, submodules, fake_ctx)
    assert "error" in json.loads(ctx.tools["cp_screen"]["handler"]({}))
    assert "error" in json.loads(ctx.tools["cp_who_owns_address"]["handler"]({"address": "1 Main", "zip": "abc"}))
    out = json.loads(ctx.tools["cp_lookup"]["handler"]({"name": "Hikvision"}))
    assert out["found"] and out["legal_name"].startswith("Hangzhou")


def test_no_environment_or_credential_file_reads():
    """Catalog rule 11: secrets only through Hermes's secret scope."""
    for path in PLUGIN_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "os.environ" not in text, path.name
        assert "getenv" not in text, path.name
        assert ".env" not in text.replace("agent.secret_scope", ""), path.name
