"""Shared external domains; request Host is only considered after allow-list match."""
import pytest

from app.models.config import SystemConfig
from app.utils.public_domains import (
    domain_origin, parse_domains, first_domain_origin, match_domain_origin,
    resolve_sso_origin,
)
from app.utils.public_base_url import resolve_download_base_url
from tests.unit.test_public_base_url import _make_request


def test_domains_csv_list_and_deduplication():
    assert parse_domains("xx.hj.com, 192.168.1.1:80\nhttps://www.hj.com") == [
        "xx.hj.com", "192.168.1.1:80", "https://www.hj.com"
    ]
    assert parse_domains(["xx.hj.com", "xx.hj.com", "192.168.1.1:80"]) == [
        "xx.hj.com", "192.168.1.1:80"
    ]
    model = SystemConfig(domains="xx.hj.com,192.168.1.1:80")
    assert model.domains == ["xx.hj.com", "192.168.1.1:80"]
    assert model.model_dump()["domains"] == model.domains


def test_global_domains_default_to_http_unless_explicit_https():
    assert domain_origin("xx.hj.com") == "http://xx.hj.com"
    assert domain_origin("192.168.1.1:80") == "http://192.168.1.1:80"
    assert domain_origin("https://corp.example.com:9443") == "https://corp.example.com:9443"
    assert first_domain_origin(["xx.hj.com", "192.168.1.1:80"]) == "http://xx.hj.com"


@pytest.mark.parametrize("bad", [
    "file:///etc/passwd", "ftp://example.com", "example.com/path",
    "http://evil.example@allowed.example", "example.com?x=y",
    "http://example.com/#anchor", "http://example.com:bad", "example.com:99999",
    "bad host", "http://example.com\rmalicious", "https://",
])
def test_domain_addresses_reject_invalid_host_or_protocol(bad):
    with pytest.raises(ValueError):
        parse_domains([bad])


def test_sso_custom_override_precedes_shared_domains():
    cfg = {"domains": ["xx.hj.com", "192.168.1.1:80"],
           "enterprise_sso_public_base_url": ""}
    assert resolve_sso_origin(cfg) == "http://xx.hj.com"
    cfg["enterprise_sso_public_base_url"] = "https://sso.example.org:9443"
    assert resolve_sso_origin(cfg) == "https://sso.example.org:9443"
    assert resolve_sso_origin({"domains": []}) == ""


def test_allowlisted_host_match_not_arbitrary_request_host():
    domains = ["xx.hj.com", "192.168.1.1:80", "https://secure.example.com"]
    assert match_domain_origin(domains, "xx.hj.com", "https") == "https://xx.hj.com"
    assert match_domain_origin(domains, "192.168.1.1:80", "http") == "http://192.168.1.1:80"
    assert match_domain_origin(domains, "secure.example.com", "http") == "https://secure.example.com"
    assert match_domain_origin(domains, "evil.example.com", "http") is None
    assert match_domain_origin(domains, "192.168.1.1:8880", "http") is None


def test_api_url_custom_then_match_then_first_then_old_fallback(monkeypatch):
    cfg = {"domains": ["xx.hj.com", "192.168.1.1:80"]}
    monkeypatch.setattr("app.core.config.load_config", lambda: cfg)
    monkeypatch.setattr("app.utils.public_base_url.get_listen_http_base",
                        lambda: "http://10.0.0.4:8881")
    assert resolve_download_base_url(None, "https://download.example.com/base") == (
        "https://download.example.com/base"
    )
    assert resolve_download_base_url(None, None) == "http://xx.hj.com"
    req = _make_request(host=b"192.168.1.1:80", scheme="http")
    assert resolve_download_base_url(req, None) == "http://192.168.1.1:80"
    spoofed = _make_request(host=b"attacker.example.com", scheme="http")
    assert resolve_download_base_url(spoofed, None) == "http://xx.hj.com"
    cfg["domains"] = []
    assert resolve_download_base_url(req, None) == "http://10.0.0.4:8881"
