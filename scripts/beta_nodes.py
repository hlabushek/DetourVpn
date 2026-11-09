"""Strict loader for the beta node inventory (ADR-094).

The inventory is the single place that says which VPN nodes exist, where, and how an operator
reaches them. It holds addresses, so the real file (`infra/beta/nodes.json`) is gitignored; only
`nodes.example.json` with documentation addresses is committed. Loading performs no network
access and never prints values: errors are fixed codes.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path

MAX_NODES = 16  # the v3 window manifest limit (ADR-038/039)
NAME = re.compile(r"^detourvpn-beta-[a-z0-9]+(?:-[a-z0-9]+)*$")
REGION = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SSH_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SSH_KEY_NAME = re.compile(r"^detourvpn-[A-Za-z0-9._-]{1,64}$")
KEYS = {
    "name",
    "region",
    "country_code",
    "city",
    "host",
    "ssh_user",
    "ssh_key",
    "ssh_port",
    "udp_ports",
}


class InventoryError(ValueError):
    """Fixed error codes only; never include an address, path or value from the file."""


@dataclass(frozen=True)
class BetaNode:
    name: str
    region: str
    country_code: str
    city: str
    host: str
    ssh_user: str
    ssh_key: Path
    ssh_port: int
    udp_ports: tuple[int, int]


def _text(value: object, code: str, *, limit: int = 64) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit or not value.isprintable():
        raise InventoryError(code)
    return value


def _node(raw: object, *, allow_non_global: bool, key_directory: Path) -> BetaNode:
    if not isinstance(raw, dict) or set(raw) != KEYS:
        raise InventoryError("inventory_node_keys_invalid")
    name = _text(raw["name"], "inventory_name_invalid")
    region = _text(raw["region"], "inventory_region_invalid")
    country = _text(raw["country_code"], "inventory_country_invalid", limit=2)
    city = _text(raw["city"], "inventory_city_invalid")
    user = _text(raw["ssh_user"], "inventory_ssh_user_invalid", limit=32)
    key_name = _text(raw["ssh_key"], "inventory_ssh_key_invalid")
    if (
        NAME.fullmatch(name) is None
        or REGION.fullmatch(region) is None
        or re.fullmatch(r"[A-Z]{2}", country) is None
        or SSH_USER.fullmatch(user) is None
        or SSH_KEY_NAME.fullmatch(key_name) is None
    ):
        raise InventoryError("inventory_value_invalid")
    try:
        address = ipaddress.ip_address(_text(raw["host"], "inventory_host_invalid", limit=15))
    except ValueError:
        raise InventoryError("inventory_host_invalid") from None
    if address.version != 4 or (not allow_non_global and not address.is_global):
        raise InventoryError("inventory_host_must_be_public_ipv4")
    port = raw["ssh_port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise InventoryError("inventory_ssh_port_invalid")
    udp = raw["udp_ports"]
    if (
        not isinstance(udp, list)
        or len(udp) != 2
        or any(type(item) is not int for item in udp)
        or not 1024 <= udp[0] <= udp[1] <= 65535
        or udp[1] - udp[0] + 1 > MAX_NODES
    ):
        raise InventoryError("inventory_udp_ports_invalid")
    return BetaNode(
        name=name,
        region=region,
        country_code=country,
        city=city,
        host=str(address),
        ssh_user=user,
        ssh_key=key_directory / key_name,
        ssh_port=port,
        udp_ports=(udp[0], udp[1]),
    )


def parse_inventory(
    data: bytes, *, allow_non_global: bool = False, key_directory: Path | None = None
) -> dict[str, BetaNode]:
    """Return nodes by name; reject unknown keys, duplicates and more than 16 nodes."""
    if len(data) > 65536:
        raise InventoryError("inventory_too_large")
    try:
        document = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise InventoryError("inventory_not_json") from None
    if not isinstance(document, dict) or set(document) != {"version", "nodes"}:
        raise InventoryError("inventory_shape_invalid")
    if document["version"] != 1 or not isinstance(document["nodes"], list):
        raise InventoryError("inventory_version_invalid")
    if not 1 <= len(document["nodes"]) <= MAX_NODES:
        raise InventoryError("inventory_node_count_invalid")
    directory = key_directory if key_directory is not None else Path.home() / ".ssh"
    nodes = [
        _node(item, allow_non_global=allow_non_global, key_directory=directory)
        for item in document["nodes"]
    ]
    for field in ("name", "region", "host"):
        values = [getattr(node, field) for node in nodes]
        if len(set(values)) != len(values):
            raise InventoryError(f"inventory_duplicate_{field}")
    return {node.name: node for node in nodes}


def load_inventory(path: Path, *, allow_non_global: bool = False) -> dict[str, BetaNode]:
    try:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_size > 65536:
            raise InventoryError("inventory_file_untrusted")
        data = path.read_bytes()
    except OSError:
        raise InventoryError("inventory_file_unreadable") from None
    return parse_inventory(data, allow_non_global=allow_non_global)
