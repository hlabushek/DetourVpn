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
