# ADR-165 — Paranoid-mode audit: open items that need a larger change

**Status:** recorded; no behaviour change. Each item has a `TODO(paranoid-audit)` comment at its
location. Proven, locally fixable defects of the same audit have their own ADRs (ADR-164, and the
ADR-162 amendment).

## Scope of the audit (2026-11-21 night run)
Dart: `api.dart`, `main.dart` (restore, lifecycle), `window_connection.dart` (poll, connect,
cleanup, reconcile, adoption, reconnect/resume timers, switch, offline grace), `connection.dart`
(timers, latency), `connection_status.dart`, `connected_since.dart`, `login_screen.dart`,
`latency_probe.dart`, `always_on_recovery.dart`, `protected_api_transport.dart`, disposal of every
`Timer`, `StreamSubscription`, `AnimationController` and `TextEditingController` in `lib/`.
Android: `ClientApplication` (worker scheduling, deadline, watchdog tick, restart/give-up, probe
ladder, Doze aids, backstop, wake receivers), `ClientActivity` channel dispatch, `BootReceiver`,
`RenewAlarmReceiver`, `BackstopJobService`, `NativeRenewer`, `SecureVault`, network timeouts.

## Open items (theoretical or needing an architectural change)
1. **No Dart-side timeout on native channel calls** — `window_connection.dart`, at `refresh()`.
   `snapshot`, `connectWindow` and `advanceWindow` are awaited without a bound. A native worker
   that never answers would keep `_polling` true for good (no more polls) and make a connect tap
   wait without end (ADR-154). Not proven to happen: the worker's operations are each bounded
   today. A fix needs native-side cancellation (a `connectWindow` answered late must not leave a
   tunnel nobody tracks), so it is a design change, not a local fix.
   *Addressed Dart-side by ADR-185 (uncommitted, not on a device); no native change was needed.*
2. **Native renewal HTTP calls have no overall deadline** — `NativeRenewer.call` (platform HTTPS
   path). Connect and read timeouts are 10 s each, but DNS has no timeout and the read timeout is
   per read, so a slow-dripping answer could hold the single renewer thread past the window's end.
   Needs a per-call deadline (for example a timer that disconnects the connection).
3. **The connected timer is wall-clock based** — `connected_since.dart`. It must survive a process
   restart and Dart has no boot-relative clock, so a wall-clock step makes «Подключено …» jump.
   Display only (ADR-160 fixed the decision-relevant handshake ages). A fix needs native to report
   the tunnel start as `elapsedRealtime`.

## Checked and not a defect (for the record)
- Every `Timer.periodic` is cancelled in `dispose`/`pausePolling`; the state-event subscription is
  cancelled in `WindowConnectionController.dispose`; `_emit` is a no-op after dispose; reconnect and
  resume `Future.delayed` callbacks check `_dead` and the version.
- Out-of-order results: catalog refreshes are serialized by `nodesLoading`; polls by `_polling`;
  quota by its epoch; connect/switch by `busy`/`_switching`; double taps are absorbed by `busy` and
  `PrimaryActionGate`.
- `SecureVault`'s constructor cannot fail, so storage calls never depend on a failed native init
  (ADR-155 retry covers the key and the engine).
- Receivers finish their `goAsync` results in `finally` blocks; the backstop wake lock is bounded.

## Security and privacy
No behaviour change in this ADR. Comments only.

## Test plan
None (comments only). `flutter analyze` and the Java substitute compile the touched files.
