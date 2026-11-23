"""TEST ONLY (M5, ADR-175): the beta allowlist merge keeps every node IP. No host is touched.

Addresses are documentation ranges (RFC 5737).
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import caddy_allowlist  # type: ignore[import-not-found]  # noqa: E402
from caddy_allowlist import AllowlistError, merge  # noqa: E402

LIVE = """{
\tauto_https disable_certs
}
https://192.0.2.1 {
\t@beta_node_v3 {
\t\tremote_ip 198.51.100.10 198.51.100.11
\t\tmethod POST
\t\tpath /api/internal/v3/windows/poll
\t}
\thandle @beta_node_v3 {
\t\treverse_proxy 127.0.0.1:28000
\t}
}
"""


def remote_ips(text: str) -> list[str]:
    line = next(item for item in text.split("\n") if item.strip().startswith("remote_ip "))
    return line.split()[1:]


def test_new_node_is_appended_and_existing_nodes_are_kept() -> None:
    merged = merge(LIVE, "203.0.113.20")
    assert remote_ips(merged) == ["198.51.100.10", "198.51.100.11", "203.0.113.20"]


def test_merge_is_idempotent() -> None:
    once = merge(LIVE, "203.0.113.20")
    assert merge(once, "203.0.113.20") == once


def test_everything_except_the_matcher_line_is_unchanged() -> None:
    merged = merge(LIVE, "203.0.113.20")
    assert [line for line in merged.split("\n") if "remote_ip" not in line] == [
        line for line in LIVE.split("\n") if "remote_ip" not in line
    ]
    assert merged.split("\n").index("\t\tmethod POST") == LIVE.split("\n").index("\t\tmethod POST")


@pytest.mark.parametrize(
    ("live", "code"),
    [
        (LIVE.replace("remote_ip 198.51.100.10 198.51.100.11", ""), "caddy_remote_ip_not_unique"),
        (LIVE + "\t\tremote_ip 203.0.113.9\n", "caddy_remote_ip_not_unique"),
        (LIVE.replace("198.51.100.11", "not-an-ip"), "caddy_remote_ip_invalid"),
        (LIVE.replace("198.51.100.11", "2001:db8::1"), "caddy_remote_ip_invalid"),
        (
            LIVE.replace("remote_ip 198.51.100.10 198.51.100.11", "remote_ip"),
            "caddy_remote_ip_empty",
        ),
    ],
    ids=["no-matcher", "duplicate-matcher", "bad-entry", "ipv6-entry", "empty"],
)
def test_malformed_configuration_is_refused_with_a_fixed_code(live: str, code: str) -> None:
    with pytest.raises(AllowlistError, match=code):
        merge(live, "203.0.113.20")


def test_invalid_new_address_is_refused() -> None:
    with pytest.raises(AllowlistError, match="node_ip_invalid"):
        merge(LIVE, "203.0.113.20; reverse_proxy")


def test_cli_reads_stdin_and_writes_stdout() -> None:
    script = ROOT / "scripts" / "caddy_allowlist.py"
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(script), "merge", "203.0.113.20"],
        input=LIVE,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert remote_ips(result.stdout)[-1] == "203.0.113.20"
    refused = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(script), "merge", "bad"],
        input=LIVE,
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 1 and refused.stderr.strip() == "[caddy-allowlist] node_ip_invalid"
    assert caddy_allowlist.CODE.fullmatch("node_ip_invalid")


def test_powershell_merges_the_live_configuration_instead_of_rendering_the_template() -> None:
    text = (ROOT / "scripts/activate_beta_caddy_allowlist.ps1").read_text(encoding="utf-8")
    assert "caddy_allowlist.py" in text and "merge" in text
    assert "cat /etc/caddy/Caddyfile" in text
    # The old path wrote a template rendered with one node IP over the live file.
    assert "Replace('{$DETOURVPN_BETA_NODE_IP}'" not in text
    assert "Get-Content -Raw -LiteralPath $templatePath" not in text
