"""Synthetic lease/command tests only. Never sudo, SSH, or real firewall calls."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "scripts/i011_control.py"
SPEC = importlib.util.spec_from_file_location("i011_test_control", SOURCE)
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


@pytest.fixture
def lease():
    return {
        "schema_version": 1,
        "host": control.HOST,
        "owner": "vpn-deploy",
        "owner_uid": 1001,
        "boot_id": "synthetic-boot",
        "issued_at": 100,
        "expires_at": 2800,
        "issued_monotonic": 10,
        "expires_monotonic": 2710,
        "code_sha256": dict.fromkeys(control.SOURCES, "synthetic-hash"),
    }


def test_current_owner_and_bounded_lease_accepted(lease):
    control.validate_lease(lease, "1001", "synthetic-boot", 110, 20)


@pytest.mark.parametrize(
    "uid,boot,now,ticks",
    [
        ("1002", "synthetic-boot", 110, 20),
        (None, "synthetic-boot", 110, 20),
        ("1001", "new-boot", 110, 20),
        ("1001", "synthetic-boot", 2800, 20),
        ("1001", "synthetic-boot", 110, 2710),
        ("1001", "synthetic-boot", 90, 20),
        ("1001", "synthetic-boot", 110, 9),
    ],
)
def test_expiry_clock_rollback_reboot_or_other_user_refused(lease, uid, boot, now, ticks):
    with pytest.raises(control.ControlError):
        control.validate_lease(lease, uid, boot, now, ticks)


@pytest.mark.parametrize(
    "change",
    [
        {"expires_at": 2801},
        {"expires_monotonic": 2711},
        {"code_sha256": {}},
        {"code_sha256": {"../../arbitrary.py": "synthetic"}},
        {"owner": "root"},
        {"host": "127.0.0.1"},
        {"code_sha256": None},
    ],
)
def test_lease_cannot_widen_target_duration_or_code_set(lease, change):
    lease.update(change)
    with pytest.raises(control.ControlError):
        control.validate_lease(lease, "1001", "synthetic-boot", 110, 20)


@pytest.mark.parametrize(
    "arguments", [[], ["bash"], ["start", "extra"], ["status;id"], ["-c", "id"]]
)
def test_no_arbitrary_command_or_extra_arguments(monkeypatch, arguments):
    monkeypatch.setattr(control.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", ["i011_control.py", *arguments])
    monkeypatch.setattr(control, "command", lambda *args, **kwargs: pytest.fail("no child command"))
    assert control.main() == 1


@pytest.fixture
def installed(lease, tmp_path, monkeypatch):
    for name in control.SOURCES:
        (tmp_path / name).write_bytes(b"synthetic public code bytes")
    unit = tmp_path / "test.service"
    unit.write_bytes(b"synthetic public unit bytes")
    boot = tmp_path / "boot"
    boot.write_text("synthetic-boot")
    lease["code_sha256"] = {
        name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in control.SOURCES
    }
    lease["unit_sha256"] = hashlib.sha256(unit.read_bytes()).hexdigest()
    lease_path = tmp_path / "lease.json"
    lease_path.write_text(json.dumps(lease))
    monkeypatch.setattr(control, "ROOT", tmp_path)
    monkeypatch.setattr(control, "LEASE", lease_path)
    monkeypatch.setattr(control, "UNIT_FILE", unit)
    monkeypatch.setattr(control, "BOOT", boot)
    monkeypatch.setattr(control, "trusted", lambda path: None)
    monkeypatch.setattr(control.time, "time", lambda: 110)
    monkeypatch.setattr(control.time, "monotonic", lambda: 20)
    monkeypatch.setenv("SUDO_USER", "vpn-deploy")
    monkeypatch.setenv("SUDO_UID", "1001")
    return tmp_path


def test_installed_hashes_verified_before_operations(installed):
    assert control.authorized()["owner"] == "vpn-deploy"


@pytest.mark.parametrize("name", ["android_staging_window.py", "i011_control.py", "test.service"])
def test_code_or_unit_change_invalidates_grant(installed, name):
    (installed / name).write_bytes(b"modified synthetic bytes")
    with pytest.raises(control.ControlError):
        control.authorized()


def test_expiration_removes_exact_rule_and_lease_even_if_stop_fails(tmp_path, monkeypatch):
    rule, lease_path = tmp_path / "sudo-rule", tmp_path / "lease"
    rule.touch()
    lease_path.touch()
    unrelated = tmp_path / "unrelated"
    unrelated.touch()
    monkeypatch.setattr(control, "SUDOERS", rule)
    monkeypatch.setattr(control, "LEASE", lease_path)

    def failed_stop(argv, **kwargs):
        assert argv == ["/usr/bin/systemctl", "stop", control.UNIT]
        raise control.ControlError

    monkeypatch.setattr(control, "command", failed_stop)
    with pytest.raises(control.ControlError):
        control.expire()
    assert not rule.exists() and not lease_path.exists() and unrelated.exists()


def test_journal_receipt_excludes_unknown_fields_and_text():
    marker = "synthetic-sensitive-output"
    text = "\n".join(
        [
            marker,
            json.dumps({"endpoint_ready": True, "PrivateKey": marker}),
            json.dumps({"error": "staging_window_failed", "stage": marker, "failure_code": marker}),
            json.dumps({"cleanup_complete": True, "restored": {"links": True, marker: marker}}),
        ]
    )
    result = control.safe_events(text)
    assert marker not in json.dumps(result)
    assert result[0] == {"endpoint_ready": True}
    assert result[1]["stage"] == "unknown"
    assert result[2] == {"cleanup_complete": True, "restored": {"links": True}}


@pytest.mark.parametrize("failed", [False, True])
def test_revoke_diagnostic_keeps_fixed_stage_without_child_secrets(
    installed, monkeypatch, capsys, failed
):
    monkeypatch.setattr(control.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "argv", ["i011_control.py", "revoke"])
    monkeypatch.setattr(
        control,
        "command",
        lambda argv, **kw: json.dumps([{"addr_info": [{"local": control.HOST}]}]),
    )
    payload = (
        {
            "error": "staging_window_failed",
            "stage": "revoke_peer_remove",
            "failure_code": "check_rejected",
            "PrivateKey": "synthetic-secret",
        }
        if failed
        else {"peer_removed": True, "traffic_cutoff_verified": False}
    )
    from types import SimpleNamespace

    monkeypatch.setattr(
        control.subprocess,
        "run",
        lambda *args, **kw: SimpleNamespace(
            returncode=int(failed), stdout=json.dumps(payload), stderr="synthetic-secret"
        ),
    )
    assert control.main() == int(failed)
    output = capsys.readouterr().out
    assert "synthetic-secret" not in output
    row = json.loads(output)
    if failed:
        assert row["events"][0]["stage"] == "revoke_peer_remove"
    else:
        assert row == payload
