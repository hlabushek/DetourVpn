# Connection reliability — report (2026-11-11)

Plain English. **Nothing was pushed. Nothing was deployed. No server, VPS, panel or beta system
was touched. Nothing was verified on a physical phone.** Every result below comes from code reading,
unit tests and local builds on this computer.

Branch: `claude/connection-reliability`, cut from `claude/overnight-integration` at `ffdae8f`.
Details: `RELIABILITY_PLAN.md`, `RELIABILITY_ROOT_CAUSE.md`, ADR-117 to ADR-120.

## 1. Root cause status
| # | Finding | Status |
|---|---|---|
| A | **Opening the app stopped the VPN.** When the app UI is closed (swiped away, or Android reclaims it after a long time), the tunnel keeps running and renewing natively. On the next launch, the new UI's first sync called `native.disconnect()` and revoked the live connection. To you this looks like "the VPN dropped while the app was closed". | **Verified in code** (test reproduces it; it fails on the old code) |
| B | **The restore after a process kill always failed with 15-minute windows.** The background restore engine answered `startNativeRenewal` with "not implemented"; Dart treated that as a fatal error, so it stopped the VPN it had just restored. | **Verified in code** (test fails on the old code) |
| C | **Automatic reconnect never started** when the server was still closing the old connection, which is the normal case with a real server. The Russian text said the app was retrying. | **Verified in code** (test fails on the old code) |
| D | **Picking another server while connected or connecting did nothing**, and connecting right after a disconnect failed with `revoke_unconfirmed` until the old connection had closed. A second try worked. | **Verified in code** |
| E | **Doze leaves room for about one renewal attempt per 15-minute window.** One failure at wake-up (network not up yet, slow node) could end the window. | **Hypothesis** (strong; based on documented Android alarm limits; not measured on your phone) |
| F | **`window_not_active` at `lease_validation`** comes from a *connect*, not a renewal: the new connection left "pending" in a state other than "active". Most likely cause: the UI and the background restore engine each revoked the other's connection (they both clean up all connections of the device at start). Other possible causes need server data: node restart, stale node heartbeat, funding that ran out. | **Hypothesis** (the code path is verified; which cause happened on your phone is not) |

The server does **not** refuse a renewal that is slightly late in a way we can fix: it refuses
only inside the last 5 s of a window, and the node closes the peer at the window's end. No grace
period was added (ADR-120).

## 2. What changed
| Commit | What |
|---|---|
| `06d6e94` | API: the 5 s renewal margin is now one named constant shared with the node contract, with boundary tests. Behaviour unchanged. (ADR-120) |
| `985a4fc` | Android: the native renewer retries up to 4 times inside one wake-up (2/4/8 s apart, none after 30 s, inside the existing 60 s wake lock) before waiting for the next alarm. (ADR-118) |
| `8d0a3be` | Client: (A) a reopened UI **adopts** the running tunnel when the server's active connection matches the native connection, generation and key, otherwise stops it as before; (B) a failed native-renewal hand-off keeps Dart renewal, and the restore engine answers "not started"; (C) reconnect starts once the old connection has closed; (D) connect waits up to 10 s for the old connection to close, and choosing another server while connected or connecting switches to it; (F) new code `window_activation_refused` with its own Russian text and up to 3 automatic retries; separate, accurate texts for `window_not_active` and `window_generation_conflict`. (ADR-117, ADR-119) |
| (docs) | This report, plan, root cause, owner log steps, device tests. |

User-facing Russian texts changed:
- `window_not_active`: «Сервер завершил подключение: доступ не был продлён вовремя. VPN остановлен; приложение подключается заново. Если не получилось, нажмите «Подключить».»
- `window_activation_refused` (new): «Сервер не включил новое подключение. Приложение повторяет попытку (до трёх раз); если не получилось, нажмите «Подключить» или выберите другой сервер.»
- `window_generation_conflict`: «Сервер и приложение разошлись в данных подключения. VPN остановлен; нажмите «Подключить».»
- Status after adoption: «VPN продолжает работать, проверяется связь».

## 3. Checks run (real results)
| Check | Result |
|---|---|
| pytest `test_window_service.py`, `test_window_runtime.py`, `test_window_pool.py` (throwaway PostgreSQL) | **39 passed** (incl. new boundary test, 2 cases) |
| Ruff check + format, mypy on `window_service.py` | passed / no issues |
| `flutter analyze` (ASCII copy, and inside the Android build script) | no issues |
| `flutter test`, full client suite | **199 passed** (15 new) |
| New tests against the **old** `window_connection.dart` | 4 bug tests fail as expected (A, B ×2, C); the fail-closed tests pass |
| Gradle `:app:testDebugUnitTest` | **82 tests, 0 failures** (incl. new `burstRetriesStayInsideTheWakeUp`) |
| Gradle `:app:lintDebug` | passed |
| Android build `scripts/build_flutter_client.ps1 -WindowTunnels -WindowSeconds 900` | passed (debug + unsigned release APK) |
| Windows build `scripts/build_windows_client.ps1` | passed: Dart analyze clean, flutter test 199 passed, C++ ctest 2/2 passed, release build and unsigned zip |

One API test (`test_connection_epoch_history_restore_and_protected_downgrade`) errors unless
`VPN_TEST_POSTGRES_BIN` points at the PostgreSQL tools; with it set, it passes. Environment only.

## 4. Build artifacts (local, not for distribution)
| Artifact | SHA-256 |
|---|---|
| `apps/client/build/app/outputs/flutter-apk/app-debug.apk` | `0d841056f39fa0321d536b67126e3bdf41414d1d1be9c2843dbeabdf15c73549` |
| `apps/client/build/app/outputs/flutter-apk/app-release.apk` (unsigned, no API origin baked in) | `88882286515dcac518605009d43fc770dbc44abfefab3abe392c766a3d6a2cbb` |
| `apps/client/build/windows-zip/DetourVpn-windows-x64-0.1.0-beta.27.zip` (unsigned) | `1602e131d8db1ffe507293c0f35db7aea225b09ef06d8a5dd4ab16c6f82f04a4` |

These are test builds. For the phone tests, build a signed APK with your usual release script
(`-WindowSeconds 900`). Signing and security settings were not changed.

## 5. Not verified
- Nothing on a physical phone: Doze timing, OEM battery managers, the UI-engine lifecycle on your
  phone model, sticky restart, boot restore.
- Nothing against a real node or the beta API.
- Whether finding F's main suspect (two engines) is what happened to you. The server data in
  `RELIABILITY_OWNER_LOGS.md` will tell.

## 6. Known limits left
- The background restore engine (after a reboot or process kill) still renews from Dart timers,
  so a long Doze period after a restore can still end the connection. Fixing it needs native
  renewal there, which under Lockdown needs `VpnService.protect()` (ADR-098).
- The UI and the restore engine use two Dart API clients with one stored refresh token. A token
  rotation by one can sign the other out. Not changed; worth its own issue.
- If the network is down for the whole wake-up burst, a window can still end in deep Doze. The
  app then reconnects when it next runs.

## 7. What you need to do
1. Review the 3 commits on `claude/connection-reliability` (nothing is pushed).
2. Build a signed APK with 15-minute windows and run `RELIABILITY_DEVICE_TESTS.md` on the phone.
3. Run the read-only steps in `RELIABILITY_OWNER_LOGS.md` for the times of the drops you already
   saw, and for any drop during the new tests. Send back the shortened rows.
4. No server deploy is needed for these fixes: the API change is behaviour-neutral.
