from kalitools.apt_catalog import (
    _debtag_to_category,
    _keyword_category,
    _post_process,
)


def test_debtag_forensics():
    assert _debtag_to_category(["security::forensics"]) == "forensics"


def test_debtag_protocol_http():
    assert _debtag_to_category(["protocol::http"]) == "web"


def test_keyword_category_recon_wins():
    cat = _keyword_category("nmap", "Network scanner and discovery tool")
    assert cat == "recon"


def test_post_process_defaults_to_other_when_no_signal():
    entries = [{"name": "weirdpkg", "description": "lorem ipsum"}]
    out = _post_process(entries, membership={})
    assert out[0]["category"] == "other"
    assert out[0]["commands"] == ["weirdpkg"]
    assert out[0]["source"] == "apt"


def test_post_process_meta_membership_wins():
    entries = [{"name": "nmap", "description": "scan"}]
    out = _post_process(entries, membership={"nmap": ("recon", "Port Scan")})
    assert out[0]["category"] == "recon"
    assert out[0]["subcategory"] == "Port Scan"
