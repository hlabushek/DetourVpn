"""Tests for scripts/scan_secrets.py. Secret-shaped values are synthetic, built at runtime."""

from importlib import import_module
from typing import Any

scanner: Any = import_module("scripts.scan_secrets")


def rules(text: str) -> list[str]:
    return [finding.rule for finding in scanner.scan_text("x.txt", text)]


def test_detects_synthetic_secrets_without_echoing_them() -> None:
    token = "7391846205:" + "Qz" * 17 + "c"  # 35 chars after the colon
    wg_key = "".join(chr(65 + (i * 7) % 26) for i in range(43)) + "="
    pem = "-----BEGIN " + "RSA PRIVATE KEY-----"
    assert rules(f"bot = {token}") == ["telegram-bot-token"]
    assert rules(f"PrivateKey = {wg_key}") == ["wireguard-private-key"]
    assert rules(pem) == ["private-key-block"]
    findings = list(scanner.scan_text("x.txt", f"bot = {token}"))
    assert token not in repr(findings) and len(findings[0].fingerprint) == 10


def test_ignores_placeholders_hashes_and_public_values() -> None:
    assert rules('api_token = "your-token-goes-here-123456"') == []
    assert rules('digest = "' + "a" * 64 + '"') == []
    assert rules("postgresql://user:<password>@host/db") == []
    assert rules("PublicKey = " + "A" * 43 + "=") == []


def test_allowlist_is_by_rule_and_fingerprint(tmp_path: Any) -> None:
    path = tmp_path / "allow.txt"
    path.write_text("# c\nrule abc1234567  # reason\nbroken\n", encoding="utf-8")
    assert scanner.load_allowlist(path) == {("rule", "abc1234567")}
    assert scanner.load_allowlist(tmp_path / "missing.txt") == set()


def test_repository_has_no_unallowlisted_secrets() -> None:
    findings, files = scanner.scan_tree()
    allowed = scanner.load_allowlist(scanner.ALLOWLIST)
    open_findings = [f for f in findings if (f.rule, f.fingerprint) not in allowed]
    assert files > 100
    assert open_findings == [], [(f.path, f.line, f.rule) for f in open_findings]
