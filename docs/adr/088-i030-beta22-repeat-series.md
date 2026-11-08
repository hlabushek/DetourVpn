# ADR-088 — I-030 repeat series of beta.22 owner attempts (repeatability, Wi-Fi, renewal)

**Status:** accepted by the owner in chat on 2026-11-08 ("давай прогоним").
**Date:** 2026-11-08
**Issue:** I-030
**Follows:** ADR-087 (one LTE attempt, used, succeeded)

## Context

ADR-087 produced one successful LTE attempt (handshake, traffic, per-app egress,
clean close, debit 197,523 B). Not shown: repeatability, the owner's Wi-Fi (earlier:
no UDP reached the node), and window renewal on a session longer than the 90 s window.

## Decision

At most **three** owner attempts, run in this order, each preceded by an owner
preflight and followed by a postflight (ADR-083 sequence), with read-only node
sampling (`scripts/i030_node_readonly_monitor.py`) and ADB state reads:

1. LTE, Wi-Fi off, Chrome only, ~100 s (repeat of ADR-087).
2. Home Wi-Fi, Chrome only, ~100 s.
3. LTE, ~170 s, to cross at least one 90 s window renewal; node sampling covers it.

Rules: stop the series at the first attempt that leaves a non-closed connection or
non-zero reserve; no node or client change between attempts; no capture unless the
owner says yes per attempt; the owner presses Connect once per attempt and
Disconnect when told. Budget: up to 16 MiB per attempt (at least 32 MiB must be
available for the renewal case). Any further attempt needs a new ADR.

## Consequences and limits

- Results are evidence of repeatability on one phone, one carrier and one home
  network, not a compatibility matrix and not a launch approval.
- A Wi-Fi failure is recorded as a network-path limitation, not fixed here.
