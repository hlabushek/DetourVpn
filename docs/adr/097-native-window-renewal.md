# ADR-097 — Native window renewal with a connection-scoped token

**Status:** proposed 2026-11-11 (step C2 of the owner-approved fault-tolerance plan).
**Builds on:** ADR-096 (opt-in windows up to 900 s), ADR-039 (generation fencing), ADR-040.
**Related:** `docs/runbooks/beta-lease-expiry.md`.

## Context

Only a Dart timer renews access windows. With the screen off, Android Doze pauses that timer, and
the window expires even though the VPN foreground service is still running. Moving renewal into
the native process needs a credential there. The refresh token and access token must not live in
native code: they grant the whole account, and refresh-token rotation is already fragile.

## Decision

### Credential
- `POST /api/v1/window-tunnels/{id}/renew-token` (user access token, device-bound, owner only)
  returns a random token (`secrets.token_urlsafe(32)`). Each call **rotates** it: the previous token
  stops working at once.
- The DB stores only `keyed_digest(pepper, "window-renew", connection_id, token)` in
  `peer_connections.renew_token_digest` (migration `0023`, nullable, hex check, with a downgrade).
- The token authenticates exactly two endpoints, only through the `X-Window-Renew-Token` header:
  - `GET /{id}/native`: state and current window, no client configuration.
  - `POST /{id}/native-renew`: the same `RenewalInput` and the same `renew_owned` logic as the
    user renewal.
- It is not a bearer token for anything else. It dies when the owner revokes the connection (the
  digest is cleared), when the connection is `revoke_pending`/`closed`, or when the device is
  revoked. Every refusal is `401 renew_token_invalid`. Comparison is constant-time, and the token
  is never logged.

### Android
- `NativeRenewal` (pure, unit-tested) decides when to renew. It uses the same thresholds as Dart:
  10 of 15 minutes left, or half the byte budget used. It handles backoff with jitter (2 s to
  60 s, never past the 6 s safety margin) and accepts only this connection's next generation, with
  `window_id` equal to its own idempotency key.
- `NativeRenewer` runs on its own thread in the app process. It uses HTTPS to the same API origin,
  with system trust and no redirects, and a 64 KiB response cap. It schedules an in-process timer
  plus `AlarmManager.setAndAllowWhileIdle`, so it wakes during Doze, and holds a 60 s partial wake
  lock only around an attempt. It applies new windows through `WindowDeadline.advance` under the
  same tunnel-generation fence as `advanceWindow`.
- It stops on any 401/403/404/409/422 and records the code. Disconnect, watchdog expiry and every
  `stop()` cancel it.

### Dart
- After `connectWindow`, and only for long windows (`windowSeconds != 90`), Dart fetches a token
  and calls `startNativeRenewal`. If that fails (an old API without the route, Windows, or the
  always-on headless path), Dart-driven renewal is kept.
- While `snapshot.nativeRenewal` is true, Dart does not renew. Instead it:
  - mirrors the native deadline;
  - treats `pending` as a renewal in flight;
  - adopts windows that native renewed, even several generations ahead.

## Security considerations
- A stolen token renews one connection of one device until the connection closes or the owner
  rotates the token. It cannot read configs or other connections, and cannot create connections.
  Funding, device caps and the node fence still apply.
- Logging out does not by itself invalidate the token. The connection still has to be closed
  (user disconnect or device revoke).
- The token stays in process memory only; it is not persisted. Surviving a process restart is
  step C3 (`START_STICKY` restore).

## Limits / not verified
- Not verified on a physical device or against a real node. Doze alarm timing differs by OEM.
- Doze also suspends app network access unless the app is exempt from battery optimization.
  The app already asks for that exemption after connecting. Without it, renewals happen only in
  Doze maintenance windows, which the 15-minute window is meant to cover. This needs measuring.
- The always-on headless recovery path does not use native renewal yet (C3).
- If native refuses after it has already advanced a generation that Dart has not seen, the next
  Dart refresh can report `window_generation_conflict` and end the session. In that case it was
  not renewable anyway.
