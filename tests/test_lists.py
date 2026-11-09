import json

import pytest


def test_snapshot_loads_and_covers_the_five_lists(submodules):
    lists = submodules["lists"]
    index = lists.ListIndex.bundled()
    for list_id in lists.LIST_IDS:
        meta = index.list_meta(list_id)
        assert meta.get("name"), list_id
        assert meta.get("fetched_at"), list_id
    assert "hikvision" in index.vendor_ids
    assert set(index.vendor("hikvision")["lists"]) >= {"1260H", "FCC_COVERED", "SEC889"}


def test_every_alias_vendor_exists_in_snapshot(submodules):
    lists = submodules["lists"]
    index = lists.ListIndex.bundled()
    missing = [vid for vid in lists.ALIASES if vid not in index.vendor_ids]
    assert missing == []


@pytest.mark.parametrize("text,expected", [
    ("Order 200 cameras from Hikvision USA", {"hikvision"}),
    ("quote from Zhejiang Dahua Technology", {"dahua"}),
    ("tp-link archer router", {"tp_link"}),
    ("TPLink Deco mesh", {"tp_link"}),
    ("DJI Mavic 4", {"dji"}),
    ("sequencing on a DNBSEQ-G400", {"mgi"}),
    ("WuXi AppTec contract", {"wuxi_apptec"}),
    ("China Mobile International", {"china_telecom_carriers"}),
])
def test_alias_matching_hits(submodules, text, expected):
    index = submodules["lists"].ListIndex.bundled()
    assert {h.vendor_id for h in index.match_text(text)} == expected


@pytest.mark.parametrize("text", [
    "Dell Technologies laptops",
    "adjective: adjidji",            # DJI inside a word must not match
    "the ztehub project",            # ZTE inside a word must not match
    "bgillespie@example.com",        # BGI as a prefix of a longer token
    "Microsoft Surface",
    "",
])
def test_alias_matching_misses(submodules, text):
    index = submodules["lists"].ListIndex.bundled()
    assert index.match_text(text) == []


def test_list_filter_limits_hits(submodules):
    index = submodules["lists"].ListIndex.bundled()
    hits = index.match_text("Hytera radios", only_lists=["UFLPA"])
    assert hits == []
    hits = index.match_text("Hytera radios", only_lists=["SEC889"])
    assert len(hits) == 1 and hits[0].lists == ["SEC889"]


def test_contested_status_is_carried(submodules):
    index = submodules["lists"].ListIndex.bundled()
    hits = index.match_text("Novogene sequencing")
    assert hits and hits[0].status in ("contested", "listed", "enjoined")


def test_replace_from_api_swaps_membership(submodules):
    lists = submodules["lists"]
    index = lists.ListIndex.bundled()
    index.replace_from_api({"SEC889": {"name": "Section 889", "fetched_at": "2026-12-01T00:00:00Z",
                                       "vendors": [{"id": "zte", "name": "ZTE", "status": "listed"}]}})
    assert index.vendor_ids == ["zte"]
    assert index.match_text("ZTE Corporation")[0].lists == ["SEC889"]
    assert index.pulled_at().startswith("2026-12-01")


def test_snapshot_is_valid_json_with_provenance(submodules):
    lists = submodules["lists"]
    data = json.loads(lists.DATA_PATH.read_text(encoding="utf-8"))
    assert data["source"].startswith("https://shop.tensorust.com/")
    assert data["generated_at"]
