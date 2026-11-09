import urllib.error

from conftest import CLEAN_PURCHASE, DELL_ORG, HIKVISION_ORG, HIKVISION_PURCHASE, canned_api


def _screener(submodules, responses, **kwargs):
    client = submodules["client"].TensorustClient(timeout=2, opener=canned_api(responses, **kwargs))
    return submodules["screen"].Screener(client, submodules["lists"].ListIndex.bundled(), budget_seconds=3)


def test_api_hit_merges_flags_and_ofac(submodules):
    screener = _screener(submodules, {"exposure_screen_purchases": HIKVISION_PURCHASE,
                                      "lookup_organization": HIKVISION_ORG})
    result = screener.screen("Hikvision USA Inc", amount=48000)
    assert result.hit and not result.offline
    assert result.severity == "review"
    by_vendor = {f.vendor_id: f for f in result.findings}
    assert set(by_vendor["hikvision"].lists) == {"1260H", "FCC_COVERED", "SEC889"}
    assert by_vendor["hikvision"].source == "Tensorust API"
    assert any(f.get("list") == "SEC889" for f in by_vendor["hikvision"].flags)
    ofac = [f for f in result.findings if f.lists == ["OFAC"]]
    assert ofac and ofac[0].flags[0]["citation"].startswith("31 CFR")
    assert result.organization["legal_name"].startswith("Hangzhou Hikvision")
    assert result.lists_pulled.startswith("2026-11-06")


def test_clean_name_is_clear(submodules):
    screener = _screener(submodules, {"exposure_screen_purchases": CLEAN_PURCHASE, "lookup_organization": DELL_ORG})
    result = screener.screen("Dell Technologies")
    assert not result.hit and result.severity == "clear" and not result.offline
    assert result.organization["ticker"] == "DELL"


def test_offline_falls_back_to_snapshot_and_says_so(submodules):
    boom = urllib.error.URLError("no route to host")
    screener = _screener(submodules, {"exposure_screen_purchases": boom, "lookup_organization": boom})
    result = screener.screen("Hikvision USA Inc")
    assert result.offline and result.hit
    assert result.findings[0].source == "bundled snapshot"
    assert result.findings[0].flags[0]["citation"]
    text = submodules["screen"].format_findings([result])
    assert "bundled list snapshot" in text


def test_offline_clean_name_is_not_a_false_clear(submodules):
    boom = urllib.error.URLError("down")
    screener = _screener(submodules, {"exposure_screen_purchases": boom, "lookup_organization": boom})
    result = screener.screen("Dell Technologies")
    assert result.offline and not result.hit
    assert result.errors


def test_lists_setting_filters_findings(submodules):
    screener = _screener(submodules, {"exposure_screen_purchases": HIKVISION_PURCHASE,
                                      "lookup_organization": HIKVISION_ORG})
    screener.lists = ["UFLPA"]
    result = screener.screen("Hikvision USA Inc")
    assert not result.hit


def test_client_caches_and_sends_bearer_only_with_key(submodules):
    calls = []
    client = submodules["client"].TensorustClient(timeout=2, opener=canned_api({"lookup_organization": DELL_ORG},
                                                                               calls=calls),
                                                  api_key_getter=lambda: None)
    client.call("lookup_organization", {"name": "Dell"})
    client.call("lookup_organization", {"name": "Dell"})
    assert len(calls) == 1
    assert "Authorization" not in calls[0][2]
    keyed = submodules["client"].TensorustClient(timeout=2, opener=canned_api({"lookup_organization": DELL_ORG},
                                                                              calls=calls),
                                                 api_key_getter=lambda: "tdk_test")
    keyed.call("lookup_organization", {"name": "Dell"}, full=True)
    assert calls[-1][2]["Authorization"] == "Bearer tdk_test"
    assert calls[-1][1]["full"] is True


def test_client_raises_on_bad_envelope(submodules):
    client_mod = submodules["client"]
    import pytest
    with pytest.raises(client_mod.TensorustUnavailable):
        client_mod._parse_envelope("<html>502</html>")
    with pytest.raises(client_mod.TensorustUnavailable):
        client_mod._parse_envelope('{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"x"}}')
    assert client_mod._parse_envelope('data: {"result":{"content":[{"type":"text","text":"{\\"a\\":1}"}]}}') == {"a": 1}


def test_format_findings_names_list_and_citation(submodules):
    screener = _screener(submodules, {"exposure_screen_purchases": HIKVISION_PURCHASE,
                                      "lookup_organization": HIKVISION_ORG})
    text = submodules["screen"].format_findings([screener.screen("Hikvision USA Inc")], header="h")
    assert text.startswith("h\n- Hikvision USA Inc: Hikvision (video surveillance) is on ")
    assert "FAR 52.204-25" in text and "Source: Tensorust public-records ontology" in text
