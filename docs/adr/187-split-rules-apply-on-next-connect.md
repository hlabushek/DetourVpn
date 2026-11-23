# ADR-187: Split-tunnel rules are stored while connected and apply at the next connect

Status: accepted (owner approval 2026-11-23). Supersedes the "never change rules while connected"
rule in the split-tunnel write path (Android `splitWrite`, Windows `TunnelController::WriteSplit`).

## Context

Split rules were refused while the tunnel was up. The settings screen therefore forced a
disconnect before the editor opened, and the owner reported that the editor still did not open
on the first try and that it was unclear whether the VPN was really off.

## Decision

1. Writing split rules is allowed while the tunnel is up. The write only stores the rules.
2. The running tunnel keeps the config it was built with. The engine config is built only at
   connect (Android `routedConfig` on `connect`/`connectWindow`; Windows connect path reads prefs
   when building), so the stored rules apply at the next connect, including automatic recovery
   after the tunnel drops.
3. The settings screen no longer disconnects. After leaving the split editor, if a change was
   saved while connected, it offers "Перезапустить" (restart on the same server, keeping the
   user's stay-connected wish via `WindowConnectionController.restartTunnel`) or "Позже".
4. The DNS choice (ADR-106) is unchanged and still locked while connected.

## Security notes

- A saved change cannot alter a running tunnel's routing, so the running tunnel's fail-closed
  behaviour is unchanged.
- Validation (package names, domain rules, mode, count limits) runs on every write, as before.
- The stored rules are read at the next connect, so a dropped tunnel that recovers
  automatically will use the newest saved rules, not the ones it had before the drop.
  The UI says this ("применятся при следующем подключении").

## Verification status

- Dart: updated `window_connection_test.dart` and `split_apps_test.dart`.
- Windows: `adapter_tests.cpp` updated to expect the write to succeed while up. Not built here.
- Android: `ClientActivity.java` guard removed. Not built or run on a device here.
- Not verified: that no other code path reads the `routing` preferences while the tunnel runs
  (checked by grep: `routedConfig` and `selectedPackages` are called only from connect paths).
  See "Native review" below for a line-level read of the recovery paths.
- Dart (2026-11-23): `window_connection_test.dart` covers `restartTunnel` (same node, native
  intent kept, no-op without a lease or during a switch) and the apply dialog («Позже» keeps
  the tunnel, «Перезапустить» restarts it, no dialog when the change was made while off).
  TEST ONLY fakes; no device or engine evidence.

## Native review (2026-11-23, read-only, at commit e56f692)

Question: does any native path rebuild the tunnel config from the stored `routing`
preferences while a tunnel is still supposed to be running, other than a fresh connect?

Finding: **no such path was found** on Android or Windows. This is a code reading, not a
device test.

Android (`apps/client/android/app/src/main/java/org/example/vpnpoc/`):

- The only place that reads the `routing` preferences to build a tunnel config is
  `ClientApplication.routedConfig` (`ClientApplication.java:1626`, with `selectedPackages` at
  `:1618`). It has exactly two callers, both fresh-connect paths:
  - `ClientActivity` channel `connect`/`connectWindow` (`ClientActivity.java:486-494`); the guard
    at `:491` throws when `app.provider.isUp()`.
  - `ClientApplication.handleRecoveryCall` (headless restore engine) `connectWindow`
    (`ClientApplication.java:1413-1420`); the guard at `:1416` throws when `provider.isUp()`.
  So neither can build a config while the provider reports the tunnel up.
- The in-place restart used by the watchdog (`ClientApplication.java:1097`) and the probe
  ladder (`:1200`) is `ClientApplication.restartTunnel` (`:1119`, a private native method
  unrelated to the Dart `restartTunnel`). It calls `provider.restartInPlace()` (`:1124`), which is
  `AwgProviderV1.restartInPlace` -> `GoBackend.restartInPlace` (patch text in
  `apps/android/patches/go-only.patch:134-145`). That reuses the in-memory `currentConfig` built
  at connect; it does not read preferences. A running tunnel therefore keeps its old rules
  across watchdog and probe restarts.
- `evaluateRecovery` (`:677`), `freshSignal` (`:567`), the backstop job (`:797`) and
  `giveUpWindow` (`:1140`) do not build a config. `giveUpWindow` stops the window; recovery then
  goes through the restore engine's `connectWindow`, which is a fresh connect after a full stop.
- `ClientActivity` `splitWrite` (`ClientActivity.java:412-452`; the DNS write at `:393`
  is still refused while up) only commits the preferences; it
  does not restart, notify or touch the provider.

Windows (`apps/client/windows/runner/tunnel_controller.cpp`):

- `LoadPrefs` is used to build an engine config only in `TunnelController::Connect` (`:336`,
  prefs read at `:353`) and `ConnectVless` (`:409`, prefs read at `:422`). Both throw when
  `engine_started_ || deadline_ != 0` (`:342`, `:412`).
- The engine gets its config once, on stdin (`LaunchLocked`, `:381-385`, `run -c stdin`), and
  the buffer is wiped afterwards. There is no native restart on Windows: `WatchLoop` (`:70-83`)
  and `Snapshot` only call `StopLocked` when the engine exits or the deadline passes. The engine
  cannot re-read the prefs while it runs.
- `WriteSplit` (`:260`) only saves the prefs.

Dart (`apps/client/lib/window_connection.dart`):

- `connect()` while a tunnel is up either adopts the running tunnel without rebuilding it
  (`_reconcile`, `:640`; adoption at `:933-937`) or, when a stale lease is held, stops native
  first via `_closeOldLease` -> `_cleanup` (`:838-850`, called at `:939`) before
  `connectWindow`. Native would also refuse a start while up (guards above).

Consequence to keep in mind (not a live-session rebuild, but user-visible): every automatic
recovery that goes through a full stop applies the newest stored rules without the user
choosing «Перезапустить». This includes Android `giveUpWindow` (watchdog/probe `NEW_LEASE`, a
failed `restartInPlace`) followed by native or UI recovery, a Dart auto-reconnect after
`window_not_active`, and a Windows reconnect after `engine_exited` or `lease_expired`. A user
who chose «Позже» can therefore see the new rules take effect at an unpredictable moment. This
matches decision 2 and the UI text, but it is not announced at that moment.

Not checked: the Reality/libbox engine path (`LibboxConfigFactory`). It is not called from
`ClientApplication` or `ClientActivity` today; if it is wired in, it needs the same review.
