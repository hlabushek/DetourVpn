"""ADR-116 subscription bodies. TEST SYNTHETIC samples only: random UUIDs/keys, reserved names."""

import base64
import secrets
from uuid import uuid4

import pytest

from vpn_api.panel_sources import (
    MAX_SUBSCRIPTION_BYTES,
    MAX_SUBSCRIPTION_LINKS,
    PanelLinkError,
    parse_subscription_body,
)


def vless(host: str = "panel.example.test") -> tuple[str, str]:
    uuid = str(uuid4())
    pbk = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    return (
        f"vless://{uuid}@{host}:443?type=tcp&security=reality&pbk={pbk}"
        "&fp=chrome&sni=www.example.test&sid=0a1b&flow=xtls-rprx-vision#Тест"
    ), uuid


OTHER = [
    "vmess://eyJ2IjoiMiJ9",
    "trojan://secret@trojan.example.test:443#t",
    "ss://YWVzLTI1Ni1nY206cGFzcw@ss.example.test:8388#s",
    "hysteria2://pw@hy.example.test:443#h",
]


def body(*links: str) -> str:
    return "\n".join(links)


def test_plain_newline_joined_links() -> None:
    first, uuid = vless()
    second, _ = vless("other.example.test")
    result = parse_subscription_body(
        body(first, *OTHER, second).encode("utf-8").replace("Тест".encode(), b"T")
    )
    assert [link.uuid for link in result.links][0] == uuid
    assert [link.host for link in result.links] == ["panel.example.test", "other.example.test"]
    assert result.skipped_other_protocol == len(OTHER)
    assert result.skipped_invalid == {}


@pytest.mark.parametrize("pad", [True, False])
@pytest.mark.parametrize("separator", ["\n", "\r\n"])
def test_standard_base64_body_as_3x_ui_and_remnawave_emit_it(pad: bool, separator: str) -> None:
    first, _ = vless()
    plain = separator.join([first.replace("#Тест", "#a"), *OTHER])
    encoded = base64.b64encode(plain.encode()).decode()
    if not pad:
        encoded = encoded.rstrip("=")
    result = parse_subscription_body(encoded.encode())
    assert len(result.links) == 1 and result.skipped_other_protocol == len(OTHER)
    # Line-wrapped base64 (some tools wrap at 76 characters) is accepted too.
    wrapped = "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
    assert len(parse_subscription_body(wrapped.encode()).links) == 1


def test_invalid_vless_lines_are_counted_by_code_not_returned() -> None:
    good, _ = vless()
    good = good.replace("#Тест", "")
    bad_security = good.replace("security=reality", "security=tls")
    ipv6 = good.replace("@panel.example.test:", "@[2001:db8::1]:")
    result = parse_subscription_body(body(good, bad_security, ipv6).encode())
    assert len(result.links) == 1
    assert result.skipped_invalid == {"panel_link_security": 1, "panel_link_host": 1}


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"", "panel_subscription_empty"),
        (b"   \n ", "panel_subscription_empty"),
        (b'{"outbounds": []}', "panel_subscription_format"),
        (b"proxies:\n  - name: a\n", "panel_subscription_format"),
        (b"not base64 !!", "panel_subscription_format"),
        (base64.b64encode(b"just text, no links"), "panel_subscription_format"),
        ("vless://ключ".encode(), "panel_subscription_format"),
        (b"\xff\xfe", "panel_subscription_format"),
        (b"A" * (MAX_SUBSCRIPTION_BYTES + 1), "panel_subscription_too_large"),
    ],
    ids=["empty", "blank", "json", "yaml", "garbage", "b64-no-links", "non-ascii", "binary", "big"],
)
def test_refused_bodies_have_stable_codes(raw: bytes, code: str) -> None:
    with pytest.raises(PanelLinkError) as error:
        parse_subscription_body(raw)
    assert error.value.code == code


def test_too_many_links_is_refused() -> None:
    raw = "\n".join(["trojan://x@t.example.test:443"] * (MAX_SUBSCRIPTION_LINKS + 1))
    with pytest.raises(PanelLinkError) as error:
        parse_subscription_body(raw.encode())
    assert error.value.code == "panel_subscription_too_large"


def test_result_repr_and_errors_never_contain_credentials() -> None:
    link, uuid = vless()
    result = parse_subscription_body(link.replace("#Тест", "").encode())
    assert uuid not in repr(result)
    # A rejected link is reported only by its stable code.
    bad = link.replace("#Тест", "").replace("fp=chrome", "fp=netscape")
    rejected = parse_subscription_body(bad.encode())
    assert rejected.links == [] and rejected.skipped_invalid == {"panel_link_fingerprint": 1}
    assert uuid not in repr(rejected)
