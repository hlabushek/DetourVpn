"""ADR-102 UDP latency echo: reply shape and rate limits. TEST ONLY, no sockets opened."""

import importlib
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "node-agent"))
    return importlib.import_module("latency_probe")


def test_reply_echoes_the_nonce_and_is_never_larger(probe: ModuleType) -> None:
    request = b"DVP1" + bytes(range(12))
    reply = probe.answer(request)
    assert reply == b"DVR1" + bytes(range(12)) and len(reply) == len(request)
    for bad in (b"", b"DVP1", b"DVP1" + bytes(13), b"XXXX" + bytes(12), request + b"!"):
        assert probe.answer(bad) is None


def test_per_source_bucket_refills_and_bounds_bursts(probe: ModuleType) -> None:
    limiter = probe.Limiter(0.0)
    assert sum(limiter.allow("192.0.2.1", 0.0) for _ in range(50)) == 20  # burst
    assert limiter.allow("192.0.2.2", 0.0)  # other sources are independent
    assert not limiter.allow("192.0.2.1", 0.05)
    assert limiter.allow("192.0.2.1", 0.2)  # ~2 tokens refilled after 200 ms


def test_global_cap_and_bounded_table(probe: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "GLOBAL_PER_S", 5)
    monkeypatch.setattr(probe, "TABLE_LIMIT", 3)
    limiter = probe.Limiter(10.0)
    allowed = [limiter.allow(f"198.51.100.{n}", 10.0) for n in range(10)]
    assert allowed == [True] * 5 + [False] * 5
    assert len(limiter.buckets) <= 3
    assert limiter.allow("198.51.100.99", 11.0)  # a new second resets the global budget
