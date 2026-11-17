# ADR-147 — Surviving Doze overnight (connection-survivability Phase 3)

**Status:** implemented on branch `claude/connection-reliability` as Phase 3 of the owner-approved
connection-survivability plan; committed locally only. **Not deployed, not verified on a device,
server change not verified by tests.**
**Builds on:** ADR-097 (native renewal), ADR-118 (burst attempts), ADR-120 (no grace after
`not_after`), ADR-124/125 (supervisor, recovery alarm), ADR-134 (background-start refusal),
ADR-140 (renewal without the app), ADR-142 (renew 2 min after the grant), ADR-143 (fresh signals),
ADR-144 (native state, single owner).
**Owner decision (2026-11-17):** a longer server window than 900 s — **yes**, as far as safely
possible.

## Problem
Each tunnel is a lease ("window") of at most 900 s. A renewal missed during Doze ends the tunnel
at `not_after`, with no grace (ADR-120). Today:
- every renewal and recovery alarm is inexact (`setAndAllowWhileIdle`), which deep Doze can defer
  by about 9 min;
- between tunnels no foreground service runs, so a cached process gets wake and network
  callbacks late or not at all;
- nothing re-checks a wanted-but-down VPN if an OEM task killer drops the app's alarms.

## Decision

### 3.1 Server: raise the window ceiling from 900 s to 3600 s
- `services/api/src/vpn_api/tunnels/v3_contracts.py`: `MAX_WINDOW_SECONDS = 3600` (request
  validation for `ConnectionInput`/`RenewalInput`, grant validation, node dispatch bound in
  `window_runtime.py`).
- `services/node-agent/window_gate.py`: `MAX_WINDOW_MS = 3_600_000` (slot gate and pool gate,
  `window_pool.py` imports it). `test_window_daemon.py` pins the equality of both bounds.
- `packages/contracts/openapi.json`: `window_seconds.maximum` 900 → 3600 for both inputs (edited by
  hand; the export script was not run).
- The default stays 90 s and clients keep asking for 900 s. **The client does not request longer
  windows in this change.** A client must not ask for more than 900 s until every API instance
  **and** every node agent run the new bound: an old node refuses a longer grant
  (`dispatch_deadline_exhausted` / `invalid_window_duration`) and the connection fails. Switching
  the client (`windowSeconds`, `NativeRenewal` and `WindowDeadline.MAX_MS` bounds, the Dart
  `maxWindowMs`) is a separate, later step after the deploy.

Consequences (by code reading):
- **Byte reservation and quota.** A window reserves `budget_bytes` (64 KiB … 64 MiB, unchanged)
  from the user's funding lots for its whole life; the reservation is settled from the node's
  closed-gate receipt. The size of a reservation does not change, but it is held up to 4× longer,
  so one device's held bytes are unavailable to other windows of the same user for longer. The
  per-window byte cap does not grow with the window: a heavy user still exhausts a 64 MiB budget
  in the same time and must renew on bytes (the Dart controller renews at half the budget; native
  renewal too, `NativeRenewal.due`). `funding_deadline` still caps `not_after` at the earliest
  expiry of the funding lots used, so a subscription or add-on that expires mid-window still ends
  the window on time.
- **Revocation.** A revoke command closes the gate at once when the node is reachable. If the node
  cannot be reached, the peer now stays usable up to 60 min (was 15 min) after the last grant, for
  a revoked device, a blocked user or a refund. This is the main security cost of the change.
- **Node nft timeout.** The gate opens the `live` set element with `timeout <ms>`; it is now up to
  3 600 000 ms (minus the 6 s dispatch margin). nft supports such timeouts; the quota and counter
  are unchanged, so the closed-gate receipt and `kernel_budget_violation` rules are unchanged.
- **Renew token.** The connection-scoped renew token has no expiry of its own; it lives until the
  connection closes or the token is rotated. Longer windows do not change its lifetime; they only
  mean it is presented less often.
- **Heartbeat.** `EXISTING_HEARTBEAT_SECONDS` (120 s) and admission rules are unchanged.

### 3.2 Exact renewal alarm (client)
- `RENEW_WINDOW` (and only it; recovery alarms stay inexact) uses
  `setExactAndAllowWhileIdle` when `DozeSurvival.exactAlarmAllowed(sdk, canScheduleExactAlarms())`
  is true: always below API 31, otherwise when `SCHEDULE_EXACT_ALARM` is granted. Read on every
  scheduling, so a later grant or revocation takes effect.
- With an exact alarm the renewal margin is `min(ADR-142 margin, 10 min)`
  (`NativeRenewal.EXACT_RENEW_BEFORE_MS`): for a 900 s window the renewal runs 5 min after the
  grant instead of 2 min. 10 min still leaves room for one retry alarm that Doze throttles (about
  9 min between allow-while-idle alarms before Android 12).
- If the permission is denied, or the exact call throws (permission revoked since the check), the
  inexact `setAndAllowWhileIdle` fallback is used with the **earlier** ADR-142 margin (renew 2 min
  after the grant). The event log records `renew_alarm_fallback exact_refused`.
- The manifest declares `SCHEDULE_EXACT_ALARM` only. `USE_EXACT_ALARM` is not declared: our
  reading is that Play restricts it to alarm-clock and calendar apps.
- The app does not ask the user for the exact-alarm permission yet. On Android 14+ it is denied by
  default for new installs, so most such devices use the fallback until a request flow is added.

### 3.3 Companion foreground service (client)
- `RecoveryCompanionService` (not exported, `foregroundServiceType="systemExempted"`) runs only
  while `DozeSurvival.companionWanted(desired, tunnelUp, terminalStop)`: the VPN is wanted, no
  tunnel is up and the last stop was not terminal. It shows a low-importance notification
  «DetourVPN восстанавливает подключение», starts nothing, holds no wake lock, opens no socket.
- `ClientApplication.updateDozeAids()` starts or stops it after every recovery decision, window
  start (`beginWindow`, so it stops as soon as the VPN service is up) and user disconnect.
- Android may refuse it: a background start on Android 12+ throws (logged
  `companion_refused background_start`, retried at most once a minute); on Android 14+ the
  `systemExempted` type is granted only to some apps, so a start is tried there only when the
  exact-alarm permission is held (`DozeSurvival.companionMayStart`, logged
  `companion_skipped fgs_type` otherwise). Recovery never depends on it.

### 3.4 Backstop job (client)
- `BackstopJobService`: a persisted periodic **JobScheduler** job, every 15 min (the platform
  minimum), scheduled while the VPN is wanted and cancelled when it is not. A run (after the
  ADR-144 startup settle if it started the process) calls `freshSignal("backstop")` only when
  `DozeSurvival.backstopSignal(desired, tunnelUp, attemptInFlight)`; the supervisor's own rules
  (owner, network, 30 s gap) still apply.
- **WorkManager was not added.** It is not a dependency of `apps/client/android`, the Gradle build
  uses dependency locking, and the lockfiles cannot be regenerated without running Gradle (outside
  the allowed checks). JobScheduler is a platform API (API 21+, minSdk is 24) and is what
  WorkManager uses on API 23+; no licence or supply-chain change.

### 3.5 WakeLocks stay bounded
No unbounded lock is added. Existing locks are unchanged (renewal 60 s, window 180 s, restore
45 s, wake signal settle + 3 s). The backstop holds `detourvpn:backstop` for at most 10 s and
releases it as soon as its check ran. The companion holds none.

## Assumptions
- 3600 s is a balance between fewer overnight renewals (about 8 instead of 32 per 8 h night once
  the client asks for it) and the revocation and reservation costs above. It can be lowered
  without a migration (it is only a validation bound).
- `setExactAndAllowWhileIdle` in Doze is delivered on time but throttled (Android 12+: roughly a
  minute apart; older: about 9 min). Not measured on our devices.
- The `systemExempted` FGS type is accepted for an app holding `SCHEDULE_EXACT_ALARM`; this is our
  reading of the platform rules and is not verified.

## Security and privacy
- Server: longer windows lengthen the worst case for an unreachable node after a revoke (15 → 60
  min) and hold reservations longer. Byte caps, quota checks, funding-lot expiry, receipt
  validation and idempotency are unchanged. No new endpoint, field or stored data.
- Client: no new network call, exported component, stored data or secret. The companion and the
  job are not exported (the job is bound only with `BIND_JOB_SERVICE`). Event-log entries are codes
  only (`companion_start`, `companion_stop`, `companion_refused <code>`,
  `companion_skipped fgs_type`, `backstop`, `renew_alarm_fallback exact_refused`).
- Lockdown and fail-closed behaviour are unchanged: the native deadline still stops an expired
  window; a companion or a backstop never keeps a window alive.
- **Known crash risk (unverified):** if Android lets `startForegroundService` succeed but then
  refuses `startForeground` (for example the FGS type), the service stops itself; Android may
  still report "did not call startForeground" and end the process. The `companionMayStart` gate
  is meant to avoid that case. Must be checked on Android 14+ devices.

## Known limitations — NOT verified
- **No device run.** No APK was built, nothing was installed, no Doze, standby or OEM-killer
  scenario was run. Real overnight behaviour is unknown.
- **No Gradle run.** Manifest merge, lint and `testDebugUnitTest` were not run (no wrapper in this
  worktree); the Java substitute below is a deviation, not a Gradle pass.
- **Server tests were not run** (pytest is outside the allowed checks for this task). The server
  and node-agent change and the edited tests (`test_window_service.py`,
  `test_window_daemon.py`) are unverified. Nothing was deployed.
- **Play policy** for `SCHEDULE_EXACT_ALARM` / `USE_EXACT_ALARM` and for the `systemExempted`
  FGS type must be checked by the owner before any Play Store release (TODO in
  `NativeRenewer.schedule` and the manifest).
- No request flow for the exact-alarm permission; Android 14+ new installs use the fallback.
- The Dart controller's own renewal (only when native renewal is off, UI in front) keeps the old
  margin.

## Verification
- Java (deviation from `gradlew testDebugUnitTest`): all app Java sources compiled with
  `javac --release 17` against Android SDK 36 (client and shared trees, patched tunnel sources,
  generated registrant stubbed); JUnit 4.13.2 — **209 tests passed** (206 in the main run plus 3
  `RestartInPlacePatchTest` from `apps/client/android/app`). New: `DozeSurvivalTest` (7: exact
  alarm permission rule, exact vs fallback margin, short windows, companion gap and start gate,
  backstop rule and its path through the supervisor).
- Dart: no Dart change. `flutter test` from `apps/client` — **310 passed**.
- `flutter analyze` on an ASCII-path copy (lib, test, pubspec, analysis options): no code issues;
  the one expected warning that `assets/orbit/` was not copied.
- Server: not run (see above).

## Owner steps (NOT VERIFIED — after building from this branch)
Record only pass/fail and the event log from the diagnostic report.
1. Server: run the API and node-agent test suites, then deploy API and every node agent before
   any client asks for windows longer than 900 s.
2. Android 14+: grant «Будильники и напоминания» (exact alarms) to the app, connect, screen off
   overnight; the event log shows renewals about every 5 min of window time and no
   `renew_alarm_fallback`.
3. Same without the permission: renewals still happen (fallback); compare gaps.
4. Airplane mode for 20 min with the VPN wanted: the notification «DetourVPN восстанавливает
   подключение» is shown, the event log shows `backstop` entries, and the notification disappears
   once the VPN is up again.
5. Disconnect from the app: no companion notification, no further `backstop` entries.
