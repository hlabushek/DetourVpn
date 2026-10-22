"""Synthetic secret-channel tests only; no ADB, SSH, phone, or internet calls."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "scripts/device_network_acceptance.py"
SPEC = importlib.util.spec_from_file_location("device_acceptance_test", SOURCE)
device = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(device)


def test_missing_device_result_is_retried_without_publishing_error(monkeypatch):
    rows = iter([ValueError("synthetic transient missing result"), {"stage": "probe_complete"}])

    def read():
        row = next(rows)
        if isinstance(row, Exception):
            raise row
        return row

    monkeypatch.setattr(device, "status", read)
    monkeypatch.setattr(device.time, "sleep", lambda seconds: None)
    assert device.wait_result("probe_complete", 2) == {"stage": "probe_complete"}


@pytest.mark.parametrize("failure", [False, True])
def test_profile_is_piped_to_private_storage_and_never_printed(monkeypatch, capsys, failure):
    # This marker is synthetic text, not a valid key or usable test credential.
    marker = "SYNTHETIC_NON_SECRET_CHANNEL_MARKER"
    profile = ("[Interface]\nHeaderProtectionKey = " + marker + "\n[Peer]\n").encode()

    def fake_server(action, data=None):
        if action == "status":
            return b'{"state":"active","ready":true}'
        assert action == "profile"
        return profile

    forwarded = []

    def fake_adb(*args, data=None):
        assert args[:2] == ("shell", "-T")
        assert "files/i011-profile-next.conf" in args[2]
        forwarded.append(data)
        return b""

    def finish(*args):
        if failure:
            raise device.AcceptanceError(marker)
        return {"stage": "connected_probe_complete", "native_up": True}

    monkeypatch.setattr(sys, "argv", ["device_network_acceptance.py", "connect"])
    monkeypatch.setattr(device, "server", fake_server)
    monkeypatch.setattr(device, "adb", fake_adb)
    monkeypatch.setattr(device, "write_command", lambda action: None)
    monkeypatch.setattr(device, "wait_result", finish)
    assert device.main() == (1 if failure else 0)
    assert forwarded == [profile]
    output = capsys.readouterr().out
    assert marker not in output and "HeaderProtectionKey" not in output
    assert isinstance(json.loads(output), dict)


def test_profile_with_device_private_key_is_rejected_before_transfer(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["device_network_acceptance.py", "connect"])
    monkeypatch.setattr(
        device,
        "server",
        lambda action: (
            b'{"state":"active","ready":true}'
            if action == "status"
            else b"[Interface]\nPrivateKey = synthetic-invalid\n"
        ),
    )
    monkeypatch.setattr(device, "adb", lambda *args, **kwargs: pytest.fail("must not forward"))
    assert device.main() == 1
