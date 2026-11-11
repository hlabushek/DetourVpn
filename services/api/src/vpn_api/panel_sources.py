"""Strict `vless://` + Reality share-link parser for panel servers (ADR-111).

Pure function: no DNS lookup, no network, no logging. The link carries a panel client UUID, which
is a credential; the parsed value hides credential fields from `repr`.
Subscription bodies (base64 lists, JSON/Clash) are NOT parsed here: their formats were not
verified offline (open question in ADR-111).
"""

import base64
import binascii
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

MAX_LINK_LENGTH = 4096
# uTLS client hello names accepted by sing-box `tls.utls.fingerprint`.
FINGERPRINTS = frozenset(
    {"chrome", "firefox", "edge", "safari", "360", "qq", "ios", "android", "random", "randomized"}
)
FLOWS = frozenset({"", "xtls-rprx-vision"})
TRANSPORTS = frozenset({"tcp"})
# Keys a panel may emit that carry no meaning for this client; values are still checked.
IGNORED = {"spx": re.compile(r"/[\x21-\x7e]{0,255}"), "headerType": re.compile(r"none")}
KNOWN = {"security", "pbk", "sni", "sid", "fp", "flow", "type", "encryption", *IGNORED}
REQUIRED = ("security", "pbk", "sni", "fp", "type")
DNS_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
DNS_NAME = re.compile(rf"(?=.{{1,253}}$){DNS_LABEL}(\.{DNS_LABEL})*\.[a-z][a-z0-9-]{{0,62}}(?<!-)")
IPV4 = re.compile(r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}")
SHORT_ID = re.compile(r"(?:[0-9a-f]{2}){0,8}")


class PanelLinkError(ValueError):
    """Rejected link. `code` is stable and safe to return; it never contains link content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VlessRealityLink:
    host: str = field(repr=False)
    port: int
    uuid: str = field(repr=False)
    sni: str = field(repr=False)
    public_key: str = field(repr=False)
    short_id: str = field(repr=False)
    fingerprint: str
    flow: str
    transport: str


def _fail(code: str) -> PanelLinkError:
    return PanelLinkError("panel_link_" + code)


def parse_vless_reality(raw: str) -> VlessRealityLink:
    """Parse one `vless://` Reality link or raise `PanelLinkError` with a stable code."""
    if len(raw) > MAX_LINK_LENGTH:
        raise _fail("format")
    # The fragment is a display name (may hold personal text, raw Unicode); it is dropped
    # unread and the admin types a label instead.
    link = raw.strip().partition("#")[0]
    if not link or not link.isascii():
        raise _fail("format")
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in link):
        raise _fail("format")
    try:
        parts = urlsplit(link)
    except ValueError:
        raise _fail("format") from None
    if parts.scheme != "vless" or not link.startswith("vless://"):
        raise _fail("scheme")
    if parts.path not in ("", "/"):
        raise _fail("format")
    netloc = parts.netloc
    if "@" not in netloc or netloc.count("@") != 1:
        raise _fail("uuid")
    userinfo, hostport = netloc.split("@")
    if ":" in userinfo:  # a password part is not part of VLESS share links
        raise _fail("uuid")
    try:
        uuid = UUID(userinfo)
    except ValueError:
        raise _fail("uuid") from None
    if str(uuid) != userinfo.lower():
        raise _fail("uuid")

    if hostport.startswith("["):
        raise _fail("host")  # IPv6 is out of scope for the IPv4-only beta
    host, sep, port_text = hostport.rpartition(":")
    if not sep or not host:
        raise _fail("port")
    host = host.lower()
    if not (IPV4.fullmatch(host) or DNS_NAME.fullmatch(host)):
        raise _fail("host")
    if not re.fullmatch(r"[1-9][0-9]{0,4}", port_text) or int(port_text) > 65535:
        raise _fail("port")

    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=bool(parts.query))
    except ValueError:
        raise _fail("format") from None
    query = dict(pairs)
    if len(query) != len(pairs):
        raise _fail("format")
    unknown = set(query) - KNOWN
    if unknown:
        raise _fail("unknown_field")
    for key, pattern in IGNORED.items():
        if key in query and not pattern.fullmatch(query[key]):
            raise _fail("unknown_field")
    for key in REQUIRED:
        if not query.get(key):
            raise _fail(
                {
                    "security": "security",
                    "pbk": "public_key",
                    "sni": "sni",
                    "fp": "fingerprint",
                    "type": "transport",
                }[key]
            )
    if query["security"] != "reality":
        raise _fail("security")
    if query.get("encryption", "none") != "none":
        raise _fail("format")

    public_key = query["pbk"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", public_key):
        raise _fail("public_key")
    try:
        decoded = base64.urlsafe_b64decode(public_key + "=")
    except (binascii.Error, ValueError):
        raise _fail("public_key") from None
    if len(decoded) != 32:
        raise _fail("public_key")

    sni = query["sni"].lower()
    if not DNS_NAME.fullmatch(sni):
        raise _fail("sni")
    short_id = query.get("sid", "")
    if not SHORT_ID.fullmatch(short_id):
        raise _fail("short_id")
    if query["fp"] not in FINGERPRINTS:
        raise _fail("fingerprint")
    flow = query.get("flow", "")
    if flow not in FLOWS:
        raise _fail("flow")
    if query["type"] not in TRANSPORTS:
        raise _fail("transport")

    return VlessRealityLink(
        host=host,
        port=int(port_text),
        uuid=str(uuid),
        sni=sni,
        public_key=public_key,
        short_id=short_id,
        fingerprint=query["fp"],
        flow=flow,
        transport=query["type"],
    )
