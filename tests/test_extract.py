def test_email_domain_becomes_name_candidate(submodules):
    ex = submodules["extract"]
    names, text = ex.extract_candidates({"to": "orders@hikvision.com", "subject": "PO 4471", "body": "quote"})
    assert [(c.value, c.kind) for c in names] == [("hikvision", "email_domain")]
    assert "PO 4471" in text and "quote" in text


def test_free_mail_domains_are_ignored(submodules):
    ex = submodules["extract"]
    names, _ = ex.extract_candidates({"to": ["bob@gmail.com", "ann@outlook.com", "x@proton.me"]})
    assert names == []


def test_name_fields_and_nested_dicts(submodules):
    ex = submodules["extract"]
    names, _ = ex.extract_candidates({"payment": {"recipient_name": "Hytera Communications", "amount": 5000},
                                      "note": "radios"})
    assert [(c.value, c.kind, c.field) for c in names] == [("Hytera Communications", "name", "recipient_name")]


def test_url_host_and_two_level_suffix(submodules):
    ex = submodules["extract"]
    names, _ = ex.extract_candidates({"url": "https://store.tp-link.co.uk/checkout"})
    assert [(c.value, c.kind) for c in names] == [("tp-link", "url_host")]


def test_candidates_are_capped_and_deduplicated(submodules):
    ex = submodules["extract"]
    args = {"recipients": [f"a{i}@vendor{i}.com" for i in range(20)] + ["z@vendor1.com"]}
    names, _ = ex.extract_candidates(args)
    assert len(names) == 6
    assert len({c.value for c in names}) == 6


def test_non_party_fields_do_not_become_names(submodules):
    ex = submodules["extract"]
    names, _ = ex.extract_candidates({"command": "ls -la", "path": "/tmp/x", "content": "Hikvision"})
    assert names == []


def test_tool_matches_patterns(submodules):
    ex = submodules["extract"]
    patterns = ["send_email", "*_pay", "*payment*", "laso_*"]
    assert ex.tool_matches("send_email", patterns)
    assert ex.tool_matches("zaprite_pay", patterns)
    assert ex.tool_matches("laso_send_bank_payment", patterns)
    assert not ex.tool_matches("terminal", patterns)
    assert not ex.tool_matches("write_file", patterns)
    assert not ex.tool_matches("send_email", [])
