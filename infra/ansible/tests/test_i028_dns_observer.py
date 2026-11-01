"""Synthetic-only tests; never capture real network packets here."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "staging-v3/dns_observer.py"
SPEC = importlib.util.spec_from_file_location("i028_dns_observer", PATH)
assert SPEC is not None and SPEC.loader is not None
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_linktype_discovery_accepts_tcpdump_output_streams(
    monkeypatch: pytest.MonkeyPatch, stream: str
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        kind = b"RAW" if command[-1] == "dv3s1" else b"EN10MB"
        output = b"Data link types:\n  " + kind + b" (test only)\n"
        return subprocess.CompletedProcess(
            command, 0, output if stream == "stdout" else b"",
            output if stream == "stderr" else b"",
        )

    monkeypatch.setattr(observer.subprocess, "run", fake_run)
    assert observer.discover_linktypes(1) == {"tunnel": "RAW", "uplink": "EN10MB"}


def test_linktype_discovery_rejects_failed_or_oversized_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for result in (
        subprocess.CompletedProcess([], 1, b"  RAW\n", b"secret error"),
        subprocess.CompletedProcess([], 0, b"  RAW\n", b"x" * 4097),
    ):
        monkeypatch.setattr(observer.subprocess, "run", lambda *a, _result=result, **kw: _result)
        with pytest.raises(observer.ObservationError, match="^capture_linktype_unavailable$"):
            observer.discover_linktypes(1)


def test_fixed_filter_limits_capture_to_staging_dns_transaction() -> None:
    commands = observer.capture_commands(1, {"tunnel": "RAW", "uplink": "EN10MB"})
    assert commands["tunnel"][0][7] == "dv3slot1"
    assert "dv3s1" in commands["tunnel"][0]
    assert "outer0" in commands["uplink"][0]
    for command, _ in commands.values():
        assert "src port 42428" in command[-1]
        assert "dst port 42428" in command[-1]
        assert "udp[" not in command[-1]
        assert "-w" not in command
        assert command[:4] == ["/usr/bin/timeout", "--signal=INT", "--kill-after=2s", "20s"]
        assert command[command.index("-s") + 1] in {"32", "42"}
        assert command[command.index("-c") + 1] == "64"
    for invalid in (0, 3, True, "1"):
        with pytest.raises(observer.ObservationError, match="slot_invalid"):
            observer.capture_commands(invalid, {"tunnel": "RAW", "uplink": "EN10MB"})
    with pytest.raises(observer.ObservationError, match="capture_linktype_unsupported"):
        observer.capture_commands(1, {"tunnel": "unknown", "uplink": "EN10MB"})


def test_synthetic_header_directions_and_fail_closed() -> None:
    local = "10.203.71.2"
    outgoing = "1.0 IP 10.203.71.2.42428 > 1.1.1.1.53: UDP, length 29"
    incoming = "1.0 IP 1.1.1.1.53 > 10.203.71.2.42428: UDP, length 45"
    assert observer.parse_header(outgoing, local) == "outbound"
    assert observer.parse_header(incoming, local) == "inbound"
    with pytest.raises(observer.ObservationError, match="packet_filter_mismatch"):
        observer.parse_header("1.0 IP 10.203.71.3.9999 > 1.1.1.1.53: UDP, length 29", local)
    with pytest.raises(observer.ObservationError, match="packet_header_invalid"):
        observer.parse_header("synthetic sensitive data", local)
