# ADR-191 — A held connection is not ended by recoverable answers

**Status:** accepted, **not committed** (owner review). Code in
`apps/client/lib/window_connection.dart`; tests in `apps/client/test/window_connection_test.dart`
(group "ADR-191 the connection is not ended by recoverable answers"). **Not built into an APK, not
run on a phone.** The task brief asked for "ADR-158"; that number is taken, 191 is the next free one.

## Context
Testers report the VPN switching itself off with an error after one or several hours, also in the
background, after network changes and with Always-on. Diagnostic reports showed, among others,
`revoke_unconfirmed` at `connection_reconcile` with a confirmed handshake and an open window, and
`revoke_unconfirmed` at `tunnel_monitor`. A review of the Dart controller found four paths where
an answer that does not mean "this connection is over for good" stopped the tunnel and, worse,
called `endUserIntent` (ADR-124), so neither the UI nor native recovery reconnected.

1. `_reconcile` (UI reopened, foreground after a background fault, a restore engine's connect)
   revoked old server rows and **threw `revoke_unconfirmed` whenever the server accepted the close
   with 202 `revoke_pending`**. ADR-186 already states that 202 is the normal answer. A poll then
   treated the code as terminal (stop, revoke, `endUserIntent`); a restore attempt failed and
   waited for the native backoff.
2. On Android `_reconcile` **stopped a running, healthy tunnel** whenever native renewal was off
   (its hand-off failed, or native renewal had stopped) and no restore engine held it.
3. During a poll of a running tunnel, **HTTP 503 codes** of the window routes and the storage layer
   (`storage_unavailable`, `window_node_unavailable`, `agent_unavailable`, ...) and the lease-level
   refusals `window_not_renewable` (for example after a node agent restart) and
   `window_generation_conflict` were neither transient nor auto-reconnect codes, so they ended the
   user's wish.
4. If the `renew-token` call at connect failed once (flaky network), **native renewal was never
   started again**. In the background Dart timers sleep in Doze, so the window expired at its
   deadline and the tunnel dropped.

## Decision
1. `_reconcile` no longer throws on an accepted close. The first row still closing stays held
   (`remotePending`): connect waits for it as before (`_closeOldLease`, bounded, still reports
   `revoke_unconfirmed` if it never closes); a poll shows «Восстанавливается подключение» when the
   user still wants the VPN, retries the revoke, and resumes (`_scheduleResume`) once it is closed.
2. On Android (`nativeOwnsBackgroundRecovery`) a running tunnel whose window matches the server is
   adopted even without native renewal; Dart renews it and hands renewal to native. A server
   generation one ahead of native is advanced; more than one ahead is not adopted (stopped, as
   before). Windows keeps the ADR-117 fail-closed rule.
3. The 503 codes above join `_transientApiCodes` (keep the tunnel, show the API note, start the
   ADR-162 offline grace on a failed renewal). `window_not_renewable` and
   `window_generation_conflict` join `_autoReconnectCodes` (stop, revoke, connect on a new lease).
4. A successful poll of a held window without native renewal retries the native hand-off, at most
   once per `nativeRenewalRetry` (60 s), Android UI only, never while a Dart renewal is in flight.

## Security considerations
- Nothing extends a lease. The native monotonic deadline (ADR-126), the offline grace bound
  (ADR-162, never later than the lease) and the node's own `not_after` stay the fail-closed
  boundary for every tunnel kept or adopted here.
- Adoption keeps every ADR-117 check (server row active, same connection, device, key, node, slot,
  generation not older than native). It only removes the requirement that native already renews.
- No new endpoints, identifiers, logs or stored data. The retried `renew-token` call rotates the
  token (server side) at most once a minute and only while native renewal is not running.
- Terminal codes are unchanged: lost VPN consent, auth, quota and device-cap codes still end the
  wish; `not_found` and `invalid_response` are deliberately left as they were.

## Known limitations and open items (not changed here)
- `TunnelWatchdog` gives up a window (`NEW_LEASE`, stop reason `handshake_not_established`, stage
  `native_watchdog_restart`) after two in-place restarts without a handshake, about 30 s after a
  network change. On a slow or still-validating network this tears down a tunnel that would likely
  recover by itself. Changing ADR-146's owner-approved policy needs device logs first
  (`watchdog_new_lease` in the connection event log).
- `NativeRenewer` ends native renewal on any 401/403/404/409/422 (ADR-165/L6). The window then
  expires and native recovery starts a new one; decision 4 only helps while the UI polls.
- The `revoke_unconfirmed` + `tunnel_monitor` report pair was not traced to an exact code path;
  re-check reports from a build with this change.
- Not verified on a device: Doze, OEM background limits, Always-on after reboot or process death,
  and the time to recover after each case.
