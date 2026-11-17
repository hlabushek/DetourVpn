# ADR-150 — Client accepts and requests 3600 s windows

**Status:** implemented on branch `claude/connection-reliability` (committed locally). The
server and node side is deployed (ADR-147 3.1, runbook `docs/runbooks/deploy-window-3600.md`).
**The client is not verified on a device with a 3600 s window.**
**Builds on:** ADR-147 (3600 s server and node bound), ADR-142 (renew 2 min after each grant),
ADR-144 (native state).

## Context
The API and node agents accept windows up to 3600 s (ADR-147 3.1). The client still refused
any grant longer than 900 s in three places, and its default request was 90 s, not 900 s:
- `apps/client/lib/window_connection.dart`: `maxWindowMs = 900000` (grant duration check).
- `apps/client/android/app/src/main/java/org/example/vpnpoc/WindowDeadline.java`: `MAX_MS = 900000`
  (native monotonic deadline).
- `apps/client/android/app/src/main/java/org/example/vpnpoc/NativeRenewal.java`:
  `windowSeconds > 900` rejected in the constructor.

## Decision
- Raise the three bounds to 3600 s (3600000 ms in Dart and Java).
- Update the boundary tests: 3600 s accepted, 3601 s rejected (`WindowDeadlineTest`,
  `NativeRenewalTest`), plus a Dart test that pins `maxWindowMs`.
- The renewal timing is unchanged. Renewal is `windowSeconds * 1000 - 120000` after the grant
  (ADR-142), so a longer window only means fewer renewals. The 9-minute deep-Doze alarm delay
  from ADR-142 still leaves time before expiry because renewal happens 2 minutes after each grant,
  not before expiry.
- `scripts/run_overnight_build.ps1` passes `-WindowSeconds 3600`. The default in
  `build_flutter_client.ps1` stays 90, so a build made without the script still does not ask for
  long windows.

## Why not change the default in the client
The default request (90 s) is the legacy request body (ADR-096). Changing it would send
`window_seconds` to every API, including ones that have not been updated. Only builds made with
`-WindowSeconds 3600` ask for long windows.

## Verification (local)
- `flutter test`: 316 passed (315 + the new `maxWindowMs` test).
- `dart format --set-exit-if-changed` on the changed Dart files: no changes.
- Java substitute tests (javac + JUnit, the deviation in HANDOVER.md), against the c6 SDK tree:
  246 passed in the main run, `RestartInPlacePatchTest` 3 passed, `SystemIntegrationWiringTest` 4
  passed when run from `apps/client/android/app` (it reads files relative to that folder).
  Total 253, as in HANDOVER.md.
- Not run: Gradle, lint, `flutter analyze` (see HANDOVER.md for the ASCII-copy method), any device test.

## Not verified / limits
- No device check of a 3600 s window: grant, renewal, Doze overnight, network switch.
- The node gate timeout at 3600 s (nft `timeout 3600000ms`) is covered only by unit tests.
- The PostgreSQL window tests (48) were skipped locally because no test database is set.

## Owner steps
1. Test the 3600 s build on a phone against the three nodes (Helsinki first).
2. Build with `scripts\run_overnight_build.ps1` (it now passes `-WindowSeconds 3600`).
