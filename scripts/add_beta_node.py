"""One-command wizard for adding a beta VPN node (ADR-094, stage 3).

Two steps for the owner:

* ``prepare``  adds the node to the local inventory, creates its SSH key and prints the single
               line to paste on the new server;
* ``run``      preflight, install, register, bind, activate. Each stage is skipped when it is
               already done, so a failed run can simply be started again.

It only chains the reviewed stage tools (check_candidate_node, install_beta_node,
connect_beta_node); it adds no remote logic of its own and never prints keys or addresses of the
API host. A finished run is NOT an acceptance: connect from a phone before inviting anyone.
"""

# ruff: noqa: E501, S607

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts import connect_beta_node, install_beta_node
from scripts.beta_nodes import BetaNode, InventoryError, load_inventory, parse_inventory
from scripts.check_candidate_node import CheckError, evaluate, run_remote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "infra" / "beta" / "nodes.json"
NAME_PATTERN = re.compile(r"^detourvpn-beta-[a-z0-9]{2,24}$")


class WizardError(RuntimeError):
    """Fixed codes only."""


def ssh_directory() -> Path:
    return Path.home() / ".ssh"


def add_to_inventory(path: Path, entry: dict[str, object]) -> None:
    """Append a node after validating the whole new file; write atomically."""
    document: dict[str, Any] = (
        json.loads(path.read_bytes()) if path.exists() else {"version": 1, "nodes": []}
    )
    if any(node["name"] == entry["name"] for node in document["nodes"]):
        raise WizardError("wizard_node_already_in_inventory")
    document["nodes"].append(entry)
    raw = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    parse_inventory(raw)  # fail closed on any invalid or duplicate value before writing
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".nodes-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def ensure_key(name: str) -> Path:
    key = ssh_directory() / name
    if not key.exists():
        result = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell.
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", name, "-f", str(key)],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise WizardError("wizard_ssh_keygen_failed")
    return key


def prepare(arguments: argparse.Namespace) -> int:
    name = f"detourvpn-beta-{arguments.name}"
    if NAME_PATTERN.fullmatch(name) is None:
        raise WizardError("wizard_name_invalid")
    entry: dict[str, object] = {
        "name": name,
        "region": arguments.name,
        "country_code": arguments.country.upper(),
        "city": arguments.city,
        "host": arguments.host,
        "ssh_user": "root",
        "ssh_key": name,
        "ssh_port": 22,
        "udp_ports": [51901, 51916],
    }
    add_to_inventory(arguments.inventory, entry)
    key = ensure_key(name)
    public = key.with_name(key.name + ".pub").read_text(encoding="utf-8").strip()
    print(f"Узел {name} добавлен в {arguments.inventory.name}.\n")
    print("1) В панели хостинга поставьте на сервер Ubuntu 24.04 (другие версии не подходят).")
    print("2) Подключитесь к серверу (пароль от хостинга) и вставьте ОДНУ строку:\n")
    print(
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"echo '{public}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
        "sysctl -w net.ipv4.ip_forward=1 && "
        "echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-detourvpn-forward.conf && echo OK\n"
    )
    print(
        "   Должно закончиться словом OK. Если у хостинга есть фаервол, откройте UDP 51901-51916."
    )
    print("3) Один раз подключитесь вручную, сверьте отпечаток с консолью хостинга и ответьте yes:")
    print(f"   ssh -i $HOME\\.ssh\\{name} root@{arguments.host}")
    print(
        f"4) Запуск всего остального:\n   uv run --frozen python -m scripts.add_beta_node run --node {name}"
    )
    return 0


def state_file(name: str) -> Path:
    return Path.home() / ".detourvpn" / f"beta-add-{name}.json"


def load_done(name: str) -> set[str]:
    try:
        return set(json.loads(state_file(name).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_done(name: str, done: set[str]) -> None:
    path = state_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(done)), encoding="utf-8")


def stage_preflight(node: BetaNode, inventory: Path) -> int:
    checks = evaluate(run_remote(node))
    failed = [str(item["check"]) for item in checks if item["required"] and not item["ok"]]
    if failed:
        print("Не готово: " + ", ".join(failed))
        return 1
    return 0


def stages(pool_peers: int = 0) -> list[tuple[str, str, Callable[[BetaNode, Path], int]]]:
    def install(node: BetaNode, inventory: Path) -> int:
        # ADR-101: a pool node serves up to pool_peers devices at once; 0 = one-device slot.
        pool = ["--pool-peers", str(pool_peers)] if pool_peers else []
        return install_beta_node.main(
            ["--node", node.name, "--inventory", str(inventory), *pool, "--execute"]
        )

    def connect(command: str) -> Callable[[BetaNode, Path], int]:
        def run(node: BetaNode, inventory: Path) -> int:
            return connect_beta_node.main(
                [command, "--node", node.name, "--inventory", str(inventory), "--execute"]
            )

        return run

    return [
        ("preflight", "Проверка сервера", stage_preflight),
        ("install", "Установка узла на сервер", install),
        ("register", "Регистрация узла в системе", connect("register")),
        ("bind", "Привязка (Caddy и манифест)", connect("bind")),
        ("activate", "Включение узла", connect("activate")),
    ]


def run(arguments: argparse.Namespace) -> int:
    node = load_inventory(arguments.inventory).get(arguments.node)
    if node is None or node.name == "detourvpn-beta-helsinki":
        raise WizardError("wizard_node_not_allowed")
    done = load_done(node.name)
    for key, title, stage in stages(getattr(arguments, "pool_peers", 0)):
        if key in done:
            print(f"[пропуск] {title}: уже сделано")
            continue
        print(f"[...] {title}")
        if stage(node, arguments.inventory) != 0:
            print(
                f"[ОШИБКА] {title}. Исправьте причину и запустите команду ещё раз "
                "(сделанное пропустится)."
            )
            return 1
        if key != "preflight":
            done.add(key)
            save_done(node.name, done)
        print(f"[ok] {title}")
    print("\nУзел включён. Это ещё не приёмка: подключитесь с телефона к новому узлу,")
    print("проверьте внешний IP и учёт трафика, прежде чем приглашать людей.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("prepare")
    new.add_argument("--name", required=True, help="короткое имя города, например germany")
    new.add_argument("--host", required=True, help="публичный IPv4 нового сервера")
    new.add_argument("--country", required=True, help="код страны из двух букв, например DE")
    new.add_argument("--city", required=True)
    go = commands.add_parser("run")
    go.add_argument("--node", required=True)
    go.add_argument(
        "--pool-peers",
        type=int,
        default=32,
        help="сколько устройств одновременно (ADR-101); 0 = старый режим на одно устройство",
    )
    arguments = parser.parse_args(argv)
    try:
        return prepare(arguments) if arguments.command == "prepare" else run(arguments)
    except (WizardError, InventoryError, CheckError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
