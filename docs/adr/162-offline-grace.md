# ADR-162 — Offline grace: a held lease keeps working while the API is unreachable

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Unit-test
verified only; not verified on a device or against a real API outage.**
**Part of:** the API high-availability strategy (ADR-163). Builds on ADR-144 (native state is the
status while the API is unreachable), ADR-097/140 (native renewal), ADR-161 (fallback origins).

## Context
When every API origin is unreachable (ADR-161 tried them all), the client cannot renew its access
window. What already held before this change:
- a running tunnel was not stopped by a transient renewal failure; it ran until its lease deadline
  (Dart: `_refresh` keeps it; native renewal retries transient failures inside the window);
- the status said «VPN работает, сервер временно недоступен» on Android, but the home screen hid
  it behind «VPN активен на этом устройстве» while the phase was connected;
- a tunnel that dropped (service killed, failed in-place restart) while the API was down went the
  normal way: stop, revoke (`DELETE`, fails), new lease (fails) — no VPN until the API returned,
  although the held lease was still valid.

## Decision
1. **Grace timing (pure `OfflineGrace`, `lib/offline_grace.dart`).** The grace starts at the first
   renewal attempt of a streak that fails with a transport-level code (`network_unavailable`,
   `api_unavailable`, `rate_limited`) while a renewal is due (`_renewIntent` set). It ends at the
   **earlier of** (a) one hour after that first failure and (b) the lease's own monotonic deadline.
   Any later successful poll clears it. When it ends, the controller stops the tunnel exactly as at
   lease expiry (`lease_expired` → stop native → revoke attempt → reconnect when possible). Times
   are monotonic (the controller's `Stopwatch`), never the wall clock.
2. **No lease is extended or fabricated.** The grace only bounds how long the *existing* lease may
   be used; it can only end the tunnel earlier than the lease, never later. The server and the
   node keep enforcing `not_after`.
3. **Visible degraded state.** `WindowConnectionController.offlineGrace` and
   `offlineGraceRemaining`; the status text is «Нет связи с сервером. VPN работает по текущему
   доступу ещё N мин»; the home screen shows it instead of «VPN активен на этом устройстве».
4. **Offline resume (a connect with the last known configuration).** If the tunnel drops
   (`native_down`) while the API is known to be unreachable (a transient failure since the last
   good answer, or an active grace), the lease and grace leave at least 30 s, and the controller
   still holds the lease's grant and client config, the controller starts the same lease again
   (`connectWindow` with the same config and grant). Native's deadline is the grace end (never later
   than the lease). No API call, no new key, no revoke. At most three times per streak; otherwise the
   normal path runs.
5. **A brand-new connect offline is not allowed.** Decision (per the owner's recommendation): only a
   lease already on the device may be used. A connect with no held lease needs the API (key
   challenge, admission, a new lease) and fails as before. A lease the user ended (Disconnect) is
   being revoked and is not reused.

## Native side (no code change)
Native renewal (ADR-140) already retries transient failures inside the window and stops the tunnel
only at its deadline (`enforceDeadline`). Because every window is at most 3600 s
(`WindowConnectionController.maxWindowMs`, ADR-150) and renewal first fails after the window
started, the lease's deadline is always earlier than "first failure + 1 h": native's behaviour is
already the earlier-of rule. If windows longer than one hour are ever allowed, native needs the same
one-hour cap (follow-up recorded in ADR-163).

## Trade-offs
- During an API outage a user keeps up to one window (at most one hour) of service without quota
  being re-checked by the server. That quota was already reserved for the window, so this is not
  free service beyond the reservation.
- The offline resume reuses a lease the server still considers active; if the server did revoke it
  meanwhile, the node no longer accepts the peer and the restarted tunnel shows no handshake
  (traffic is blocked, not leaked) until the window ends.
- In the background on Android native owns recovery (ADR-142/144); the offline resume runs only in
  the foreground UI controller. The headless restore engine still needs the API.
- Behaviour change: on Android, a poll that fails while renewal is due now shows the grace text
  instead of «VPN работает, сервер временно недоступен» (test updated).

## Security and privacy
Kill switch, Lockdown and fail-closed behaviour are unchanged: the tunnel stops at the earlier of
grace end and lease expiry, the native deadline is never extended, and the offline resume uses only
the key and config the device already holds. No new data is stored or logged.

## Test plan and results
`apps/client/test/offline_grace_test.dart`: pure timing (one hour after the first failure, first
failure only, earlier-of in both directions, clear, text); controller: renewal failure keeps the
tunnel and shows the degraded state, grace end disconnects (short test grace), offline resume on the
same lease with no create/delete, no resume while the API is reachable, resume bound, no brand-new
connect offline; widget: the home screen shows the degraded text.
`window_connection_test.dart` "an unreachable API keeps a working tunnel «Подключено»" updated for
the new text. `flutter test`: full run in HANDOVER.

## Not verified
No device run, no real API outage, no node behaviour for a restarted peer on the same lease.
