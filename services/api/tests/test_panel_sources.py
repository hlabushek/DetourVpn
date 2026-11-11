"""vless:// Reality parser (ADR-111). TEST SYNTHETIC values only: random UUIDs and keys,
reserved test hostnames and a TEST-NET address. No real panel link is used."""

import base64
import secrets
from uuid import uuid4

import pytest

from vpn_api.panel_sources import PanelLinkError, parse_vless_reality

PBK = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
UUID = str(uuid4())


def link(
    host: str = "panel.example.test:443",
    user: str = UUID,
    fragment: str = "#Тестовый сервер",
    **query: str,
) -> str:
    fields = {
        "type": "tcp",
        "security": "reality",
        "pbk": PBK,
        "fp": "chrome",
        "sni": "www.example.test",
        "sid": "0123abcd",
        "spx": "%2F",
        "flow": "xtls-rprx-vision",
        "encryption": "none",
        **query,
    }
    text = "&".join(f"{k}={v}" for k, v in fields.items() if v is not None and v != "DROP")
    return f"vless://{user}@{host}?{text}{fragment}"


def code(raw: str) -> str:
    with pytest.raises(PanelLinkError) as error:
        parse_vless_reality(raw)
    return error.value.code


def test_valid_3x_ui_style_link_parses_all_fields() -> None:
    parsed = parse_vless_reality(link())
    assert parsed.host == "panel.example.test" and parsed.port == 443
    assert parsed.uuid == UUID and parsed.public_key == PBK
    assert parsed.sni == "www.example.test" and parsed.short_id == "0123abcd"
    assert parsed.fingerprint == "chrome" and parsed.flow == "xtls-rprx-vision"
    assert parsed.transport == "tcp"


def test_minimal_link_with_ipv4_empty_short_id_and_no_flow() -> None:
    raw = link(
        host="192.0.2.10:8443", sid="", flow="DROP", spx="DROP", encryption="DROP", fragment=""
    )
    parsed = parse_vless_reality(raw)
    assert parsed.host == "192.0.2.10" and parsed.port == 8443
    assert parsed.short_id == "" and parsed.flow == ""


def test_repr_hides_credentials() -> None:
    text = repr(parse_vless_reality(link()))
    for secret in (UUID, PBK, "panel.example.test", "0123abcd", "www.example.test"):
        assert secret not in text


def test_fragment_display_name_is_ignored_even_with_raw_unicode() -> None:
    plain = parse_vless_reality(link(fragment=""))
    assert parse_vless_reality(link(fragment="#Мой сервер 🇳🇱")) == plain
    assert parse_vless_reality(link(fragment="#%D0%A1%D0%B5%D1%80%D0%B2%D0%B5%D1%80")) == plain


def test_uppercase_uuid_and_host_are_normalised() -> None:
    parsed = parse_vless_reality(link(user=UUID.upper(), host="Panel.Example.TEST:443"))
    assert parsed.uuid == UUID and parsed.host == "panel.example.test"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "panel_link_format"),
        ("vless://" + "a" * 5000, "panel_link_format"),
        (link().replace("@", " @"), "panel_link_format"),
        (link().replace("&fp=", "\t&fp="), "panel_link_format"),
        (link().replace("www.example", "wwwé.example"), "panel_link_format"),
        (link().replace("vless://", "vmess://"), "panel_link_scheme"),
        (link().replace("vless://", "VLESS://"), "panel_link_scheme"),
        (link(user="not-a-uuid"), "panel_link_uuid"),
        (link(user=UUID + ":password"), "panel_link_uuid"),
        (link(user=UUID.replace("-", "")), "panel_link_uuid"),
        (link().replace(UUID + "@", ""), "panel_link_uuid"),
        (link(host="[2001:db8::1]:443"), "panel_link_host"),
        (link(host="localhost:443"), "panel_link_host"),
        (link(host="bad_host.example.test:443"), "panel_link_host"),
        (link(host="192.0.2.300:443"), "panel_link_host"),
        (link(host="panel.example.test"), "panel_link_port"),
        (link(host="panel.example.test:0"), "panel_link_port"),
        (link(host="panel.example.test:65536"), "panel_link_port"),
        (link(host="panel.example.test:044"), "panel_link_port"),
        (link(security="tls"), "panel_link_security"),
        (link(security="DROP"), "panel_link_security"),
        (link(pbk="DROP"), "panel_link_public_key"),
        (link(pbk=PBK[:-1]), "panel_link_public_key"),
        (link(pbk=PBK + "AAAA"), "panel_link_public_key"),
        (link(pbk="+" * 43), "panel_link_public_key"),
        (link(sni="DROP"), "panel_link_sni"),
        (link(sni="192.0.2.10"), "panel_link_sni"),
        (link(sid="abc"), "panel_link_short_id"),
        (link(sid="0123456789abcdef00"), "panel_link_short_id"),
        (link(sid="zz"), "panel_link_short_id"),
        (link(fp="DROP"), "panel_link_fingerprint"),
        (link(fp="netscape"), "panel_link_fingerprint"),
        (link(flow="xtls-rprx-direct"), "panel_link_flow"),
        (link(type="ws"), "panel_link_transport"),
        (link(type="DROP"), "panel_link_transport"),
        (link(extra="1"), "panel_link_unknown_field"),
        (link(headerType="http"), "panel_link_unknown_field"),
        (link(spx="relative"), "panel_link_unknown_field"),
        (link().replace("&sni=", "&sni=a.example.test&sni="), "panel_link_format"),
        (link(encryption="aes"), "panel_link_format"),
        (link().replace("443?", "443/path?"), "panel_link_format"),
    ],
)
def test_rejections_have_stable_codes(raw: str, expected: str) -> None:
    assert code(raw) == expected


def test_error_text_never_contains_link_content() -> None:
    with pytest.raises(PanelLinkError) as error:
        parse_vless_reality(link(fp="netscape"))
    assert str(error.value) == "panel_link_fingerprint"
    assert UUID not in str(error.value) and PBK not in str(error.value)
