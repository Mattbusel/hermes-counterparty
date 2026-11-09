"""Live checks against the real Tensorust API: three known entities.

Run with ``pytest -m live``. They need the network and are skipped when the API does not answer.
"""

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_screener(submodules):
    client = submodules["client"].TensorustClient(timeout=15)
    try:
        client.call("describe_schema", {})
    except submodules["client"].TensorustUnavailable as exc:
        pytest.skip(f"Tensorust API not reachable: {exc}")
    return submodules["screen"].Screener(client, submodules["lists"].ListIndex.bundled(), budget_seconds=20)


def test_listed_vendor_hikvision(live_screener):
    result = live_screener.screen("Hikvision USA Inc", description="200 IP cameras", amount=48000)
    assert not result.offline
    assert result.hit and result.severity in ("review", "prohibited")
    vendor_ids = {f.vendor_id for f in result.findings}
    assert "hikvision" in vendor_ids
    hik = next(f for f in result.findings if f.vendor_id == "hikvision")
    assert {"1260H", "FCC_COVERED", "SEC889"} <= set(hik.lists)
    assert hik.source == "Tensorust API"
    assert any(flag.get("citation") for flag in hik.flags)


def test_clean_public_company_lockheed(live_screener):
    result = live_screener.screen("Lockheed Martin")
    assert not result.offline
    assert not result.hit
    assert result.organization and result.organization["found"]
    assert "sec_frames" in result.organization["sources"]


def test_fcc_licensee_with_parent_hytera(live_screener, submodules):
    result = live_screener.screen("Hytera Communications")
    assert not result.offline and result.hit
    assert {"FCC_COVERED", "SEC889"} <= {l for f in result.findings for l in f.lists}
    tctx = submodules["tools"].ToolContext(live_screener)
    import json
    card = json.loads(submodules["tools"].cp_lookup(tctx, {"name": "Hytera"}))
    assert card["found"] and card["ownership_chain"]
