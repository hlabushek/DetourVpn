# Connection reliability — plan (2026-11-11)

Branch `claude/connection-reliability`, cut from `claude/overnight-integration` at `ffdae8f`
(clean tree at start). Nothing is pushed or deployed. No server, VPS, panel or beta system is
touched.

## Owner symptoms
1. The VPN drops when the phone sleeps or the app has been closed for a long time. Battery
   optimization is disabled for the app.
2. Connecting or switching servers sometimes fails, and a second try works.
3. The diagnostic report shows `Code: window_not_active`, `Stage: lease_validation`.

## Investigation order
1. Client connect path: `apps/client/lib/window_connection.dart` (`_connect`, `_validate`,
   `_reconcile`, `_cleanup`, `_refresh`, auto reconnect). Where is `lease_validation` set, and
   which responses raise `window_not_active` there?
2. Client start-up: `apps/client/lib/main.dart` (`signedIn`, `_initializeWindowController`), and
   the headless restore `apps/client/lib/always_on_recovery.dart`.
3. Android native side: `ClientApplication.java`, `ClientActivity.java`, `NativeRenewer.java`,
   `NativeRenewal.java`, `RenewAlarmReceiver.java`, `WindowDeadline.java`, `BootReceiver.java`,
   `TunnelWatchdog.java`.
4. Server window logic: `services/api/src/vpn_api/tunnels/window_service.py`,
   `window_routes.py`, `v3_contracts.py`; node side `window_runtime.py`.
5. ADRs 096–102, which define the current design.

## Expected file changes
| Area | Files |
|---|---|
| Dart client | `lib/window_connection.dart`, `lib/native_tunnel.dart`, `lib/connection.dart`, `lib/diagnostic_report.dart`, `lib/region_picker.dart` |
| Dart tests | `test/window_connection_test.dart`, `test/always_on_recovery_test.dart` if needed |
| Android | `ClientApplication.java` (snapshot fields, recovery answers `startNativeRenewal`), `ClientActivity.java` (snapshot passthrough only if needed), `NativeRenewal.java`, `NativeRenewer.java` |
| Android tests | `NativeRenewalTest.java` |
| API | `window_service.py` (named margin constant), `tests/test_window_service.py` |
| Docs | ADR-117..120, `RELIABILITY_*.md` |

## Checks
- `pytest tests/test_window_service.py` against the throwaway PostgreSQL (`scripts/test_with_postgres.py`).
- Ruff check and format on changed Python files.
- `flutter analyze` and `flutter test` on an ASCII copy of `lib/` and `test/` (the build script does this).
- Gradle `:app:testDebugUnitTest`.
- `scripts/build_flutter_client.ps1` for the Windows client if the toolchain works.

## Security risks and how they are handled
- **Adopting a running tunnel after the UI restarts** could attach the UI to a connection it does
  not own. Mitigation: adopt only when the server lists the connection for this device's session,
  the native tunnel reports the same connection id, runtime and generation, the public key matches
  the local key, and the state is `active`. Anything else falls back to today's fail-closed path:
  stop the tunnel and revoke.
- **More renewal attempts inside one wake** could hammer the API. Mitigation: bounded burst
  (at most 4 attempts, none starting after 30 s, inside the existing 60 s wake lock), then the existing
  alarm backoff.
- **Grace period on the server**: no grace after a window's end. The node already closed the peer
  at `not_after`, so a late renewal cannot be honored without giving unpaid access. The existing
  5 s margin becomes one named constant, with boundary tests.
- **Automatic retries create windows that reserve quota**. Mitigation: a connect-time activation
  refusal retries at most 3 times in a row; ADR-099 backoff still applies.
- No new secrets, endpoints, logs or permissions. The renew token stays in memory only.

## Out of scope (reported, not fixed)
- Two Dart engines (UI and headless restore) share one stored refresh token. Rotation by one can
  invalidate the other.
- Native renewal in the headless restore path under Lockdown (needs `VpnService.protect()`).
- Longer windows than 900 s (a node contract change).
