# ADR-096 — Opt-in long access windows (up to 15 minutes)

**Status:** proposed 2026-11-10 (step C1 of the owner-approved fault-tolerance plan).
**Amends:** ADR-038 (90 s window cap), ADR-040 (Android window lifecycle).
**Related:** `docs/runbooks/beta-lease-expiry.md` (hypothesis 1: Doze stops the Dart renewal timer).

## Context

Every v3 access window was capped at 90 s, in seven places: the contract, the node runtime, the
nftables gate, the Dart client and three Java checks. Only a Dart `Timer` renews it. When Android
Doze pauses the app for more than about 50 s, the native watchdog and the node's kernel set
timeout both end the tunnel, and the user has to open the app.

## Decision

1. `MAX_WINDOW_SECONDS` becomes 900. A new optional request field, `window_seconds` (30–900,
   default 90), on `POST /window-tunnels` and `POST /window-tunnels/{id}/renew`, picks the length.
   The grant is still capped by funding expiry (`funding_deadline`).
2. Clients send the field only when built with `VPN_WINDOW_SECONDS` other than 90
   (`build_flutter_client.ps1 -WindowSeconds 900`). Existing builds and requests are unchanged.
3. The node gate (`window_gate.MAX_WINDOW_MS`) and runtime bounds follow the contract, and a test
   pins that they stay equal.
4. A long window renews once a third of it has passed (10 of 15 minutes left). That leaves
   several retries, and room for Android's roughly once-per-9-minutes Doze alarm budget, before
   expiry. 90 s windows keep the old 55 s threshold.
5. A renewal must still move `not_after` forward (contract `window_deadline_not_advanced`).
6. The per-window byte budget is unchanged: 16 MiB in builds, at most 64 MiB. Renewal still also
   triggers at half the budget.

## Rollout order (the owner runs it)

1. Update **all** node agents. They then accept windows of 900 s or less, and old API grants are
   90 s, which still pass.
2. Deploy the API. Without the field it still issues 90 s windows.
3. Ship a client built with `-WindowSeconds 900`.

Doing these out of order fails closed, never open. An old node rejects a long grant
(`dispatch_deadline_exhausted`), and an old API rejects the unknown field with 422.

## Consequences and limits

- If a node agent is stuck or offline, a revoked or unfunded user can keep data-plane access for up
  to 15 minutes instead of 90 s. A running agent still revokes immediately on poll. The kernel
  timeout remains the backstop.
- This step alone does **not** fix Doze. It only gives a native renewer time to work. That renewer
  is step C2 (native renewal without Flutter), and the next steps are C3/C4 (service lifecycle,
  network callback, handshake watchdog). Not verified on a physical device.
