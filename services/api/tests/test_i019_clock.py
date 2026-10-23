"""TEST ONLY synthetic clock/pipe metadata; never SSH or physical VPN evidence."""

import io
import subprocess
import time
from types import SimpleNamespace

import pytest
import test_i019_live as live


@pytest.mark.parametrize("offset", [0.5, 1.0, 1.001])
def test_clock_gate_keeps_one_second_bound_and_closes_both_pipes(
    monkeypatch: pytest.MonkeyPatch, offset: float
) -> None:
    base = 1_700_000_000_000_000_000
    response = str(base + round(offset * 1_000_000_000)).encode() + b"\n"
    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(b"ready\n" + response * 3))
    stopped = []
    monkeypatch.setattr(live, "ssh_args", lambda: ["TEST-ONLY-NO-SSH"])
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(live, "terminate", lambda proc: stopped.append(proc))
    monkeypatch.setattr(time, "time_ns", lambda: base)
    if offset <= 1:
        assert live.clock_bound(live.HOSTS[0]) == pytest.approx(offset)
    else:
        with pytest.raises(live.ClockBoundExceeded) as failure:
            live.clock_bound(live.HOSTS[0])
        assert failure.value.upper_bound_seconds == pytest.approx(offset)
    assert process.stdin.closed and process.stdout.closed
    assert stopped == [process]


def test_unapproved_clock_host_refused_before_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("must refuse first"))
    with pytest.raises(ValueError, match="unapproved_host"):
        live.clock_bound("127.0.0.1")
