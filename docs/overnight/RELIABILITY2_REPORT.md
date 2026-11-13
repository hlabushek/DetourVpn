# Connection rewrite (run 2) — report, 2026-11-13

Plain English. **Nothing was pushed. Nothing was deployed. No server, VPS, panel or beta system
was touched. Nothing was tested on a physical phone or an emulator.** Results below come from code
reading, unit tests and local builds on this computer.

## 1. Worktree
- Path: `.claude/worktrees/fixes-before-beta-0eeeba` (the expected one). Branch
  `claude/connection-reliability`. Start HEAD `144080f`. No other session was active (no file
  changed in the hour before the run).
- Left alone (already untracked, not mine): seven `docs/evidence/i-030-beta-apk-*.json` files.
  None of the files I changed had uncommitted changes before.
- Outside git (ignored cache): a new prepared SDK tree `.cache/i-011/sdk-go-only-c5-20261113`
  (see ADR-127). Nothing in the cache was deleted or overwritten.

## 2. Root causes
### Confirmed in code (line numbers at `144080f`)
| # | Finding | Where | Effect |
|---|---|---|---|
| 1 | **The tunnel "restart" after a network switch did nothing.** `restartInPlace()` calls `setStateInternal(UP)` while the live handle is still set, and `setStateInternal` returns at once when a handle is set ("already up"). No new interface, no new socket. | `apps/android/patches/go-only.patch` (restartInPlace hunk); prepared `GoBackend.java:334` → `:379` | Every ADR-099/122 restart since then was a no-op. Matches the owner's report: no traffic after Wi-Fi → mobile until airplane mode. |
| 2 | **Any internal stop erased "stay connected".** The channel `disconnect` cleared `resume_connected`; Dart calls it for cleanups, not only for the button. | `ClientActivity.java:209-212`; `window_connection.dart:259-262, 286-287, 723-724` | After a window ended in the background, opening the app cleaned up and the VPN stayed off until «Подключить». |
| 3 | **No background recovery.** When the native deadline ended a window (or a restart failed) with no Flutter engine attached, nothing started a new one until the app was opened or the phone rebooted. | `ClientApplication.java:112-118` (stop, nothing after), `:480-503` | A locked phone stayed without VPN for hours. |
| 4 | **Android's VALIDATED flag was required** for the watchdog and for «Подключено». | `ClientApplication.java:169, 200-209, 581`; Dart `NativeSnapshot.confirmed` | On networks where Android's connectivity check is blocked, the watchdog never acted and the UI never said connected. |
| 5 | A 250 ms deadline poll ran for the whole process life. | `ClientApplication.java:112-118` | Needless CPU wake-ups while awake. |
| 6 | A new local IP on the same network was not seen as a path change. | `ClientApplication.java:130-156` | Same as 1 on DHCP renew or mobile re-attach. |

### Plausible, not verified
- Android 12+ may refuse the background service start for recovery if the app is not exempt
  from battery optimization, or an OEM battery manager may block it (ADR-125).
- Replacing the TUN with a second `establish()` during a restart is the design from ADR-099; it
  now really runs for the first time. Its behaviour on the owner's Android version is unknown.
- Deep Doze still limits renewal to about one attempt per 9 minutes (ADR-118, unchanged).
- The UI and restore engine use two Dart API clients with one refresh token (known, unchanged).

## 3. What changed and what was kept
See ADR-124 to ADR-132. In short:
- **AWG patch (ADR-127):** `restartInPlace` clears the handle first, so a new device and new
  protected sockets are really created on the current network; old device closed afterwards;
  any failure tears down (fail closed). New prepared SDK tree; only `GoBackend.java` differs
  from the previous one, so the native libraries were reused unchanged; pinned hash updated.
- **`PhysicalNetwork` (ADR-127):** usable network = INTERNET and no captive portal; VALIDATED is
  separate; path changes include a new local address; late callbacks for old networks ignored.
- **`ConnectionSupervisor` (ADR-124/125/129):** one record of user intent, stop reason category,
  native recovery backoff (30 s → 30 min, jitter, reset after 10 min up, never offline, never
  after a terminal stop), transport state and evidence label. Native only *starts* the existing
  restore engine when no Dart controller is attached; it never extends or reuses a window.
- **Intent:** `disconnect` now only stops the engine. New channel call `setIntent
  {connected:false}` from the explicit user disconnect (and from failures nobody retries). A
  reopened UI that finds the user still wants the VPN reconnects by itself («Восстанавливается
  подключение»). The restore engine reports its failure code; terminal codes end the intent.
- **UI engine attach/detach** is reported to native; detaching is not a disconnect.
- **Deadline (ADR-126):** one-shot check plus a check on every wake-up event; the 250 ms loop is
  gone. Fail closed as before.
- **Diagnostics:** the existing report gets one native line (`VPN: transport, evidence,
  category, intent, validated, recoveries, restarts`), fixed codes only; the preview text says
  so.
- **Kept:** the worker-thread engine owner, `WindowDeadline`, native renewal and its Doze burst,
  the watchdog policy, the restore engine and hand-over, the whole Dart window contract, the UI.
  Reasons in ADR-124.
- **Not changed:** backend, OpenAPI, window model, UI screens and settings, split tunnelling.

## 4. Verification
All run on this computer, on the final tree that is committed.

| Check | Command | Result |
|---|---|---|
| Full Android gate | `scripts/build_flutter_client.ps1 -WindowTunnels -WindowSeconds 900` (ASCII snapshot outside OneDrive) | **exit 0** |
| Dart format / analyze | inside the gate | format clean; `flutter analyze`: **No issues found** |
| Dart tests | `flutter test` (whole client suite, inside the gate) | **223 passed** (215 before; 8 new: 7 intent tests, 1 restore-failure code test) |
| Android unit tests | Gradle `:app:testDebugUnitTest` (inside the gate) | **111 tests, 0 failures, 0 errors** (new: `ConnectionSupervisorTest` 13, `PhysicalNetworkTest` 9, `RestartInPlacePatchTest` 1, `DiagnosticReportTextTest` +1) |
| Android lint | Gradle `:app:lintDebug` | passed |
| SDK preparation | `python scripts/prepare_android_poc.py --target-dir build-prep/sdk-go-only-c5` (then copied into the cache) | prepared; receipt differs from `c4` only in `GoBackend.java` |
| Python / API | not run | no API file changed |
| Windows C++ | not run | Windows engine not touched |
| Emulator / phone | not run | none available |

Note: the first gate run failed only because I redirected PowerShell streams, which turned
Flutter's Gradle-version warning on stderr into a terminating error; it was re-run without that
redirection. The intermediate commits were not each built separately; the final tree was.

APKs (local test builds, not for distribution; inside
`%LOCALAPPDATA%\ProjectVpn\i013-builds\76682b95b17a422384dfc39335c63c51\apps\client\build\app\outputs\flutter-apk\`):

| Artifact | SHA-256 |
|---|---|
| `app-debug.apk` | `6627a6a7da3a2097e050159c841514b543ec7b1a211b74797ad95263409ccfec` |
| `app-release.apk` (unsigned, no API origin baked in, 900 s windows) | `8d9aeeb82275b008ba9238e2a00bfb41300691474141fe3d9ca52fba91d47239` |

For the phone, build a signed APK with your release script; signing was not touched.

## 5. Acceptance criteria
Met = the code does it and a test proves the rule; it is still **not** proven on a phone.

| Criterion | Status | Proof |
|---|---|---|
| Backgrounding the UI does not disconnect | met | `ConnectionSupervisorTest.backgroundingOrClosingTheUiDoesNotChangeIntent`; detach is not a stop (code) |
| Activity destruction does not stop the service | met (unchanged design) | engine owned by `ClientApplication`; ADR-117 adoption tests |
| Returning to the app shows the native state | met | ADR-117 adoption tests; `a reopened UI resumes when native still wants the VPN` |
| Explicit disconnect cancels recovery | met | `explicitDisconnectDuringRecoveryCancelsItForGood`, `a disconnect during the resume delay cancels it for good`, `an explicit disconnect ends the native intent before stopping` |
| No duplicate engines | met (audit) | ADR-131; single worker; `connectWindow` refuses when up |
| Wi-Fi/cellular transitions recover automatically | partly met | the restart now really happens (patch guard test, `PhysicalNetworkTest.wifiToCellularIsAChange`); device test A needed |
| Offline periods: no excessive retries | met | `noRetriesWhileOfflineAndPromptRetryWhenNetworkReturns`; watchdog needs a usable network |
| Returning connectivity triggers recovery | met | same test; network callbacks call `evaluateRecovery` |
| Rapid changes: no stale state or races | met | `rapidOscillationBumpsGenerationEachTime`, `staleLossOfAnOlderNetworkIsIgnored`; ADR-122 debounce |
| Transport recovery preserves the TUN | partly met | restart replaces the interface via `establish()` on the same service, no teardown; not observed on a device |
| Screen-off does not tear down | met | no screen-state input exists in any stop path (code) |
| No false failure from inactivity | met | keepalive 25 s; `transport` = `degraded` not failed; `transportStateIsDerivedFromFactsOnly` |
| Delayed execution does not bypass deadlines | met | `enforceDeadline` on every wake event, elapsed-realtime (ADR-126); `WindowDeadlineTest` |
| Recovery when Android resumes execution | partly met | supervisor triggers on alarm/network/UI; Android may refuse background starts |
| Minimal battery use | partly met | 250 ms poll removed; no new wakelocks; not measured |
| Health based on meaningful evidence | met | `evidenceNeverOverclaims`; ADR-128 |
| False health failures do not storm | met | watchdog backoff 2 → 10 min (existing tests) |
| Broken connection not shown healthy forever | partly met | handshake-based; a node that handshakes but drops data is not detected |
| Stale async results cannot corrupt state | met | generations: app, intent, network; tests above |
| No unauthorized window extension / quota bypass | met | unchanged `WindowDeadline`, recovery always asks the API for a new window (ADR-130) |
| No unprotected socket routing | met (unchanged) | `protect()` failure tears down |
| No unintended DNS/IPv6 bypass | not verified | expectation from code (ADR-132); no leak test |
| Safe cleanup after revocation/terminal errors | met | `terminalFailuresEndIntentAndNeverRetry`; `a manual connect that fails for good ends the intent` |
| No misleading leak guarantees | met | ADR-132; no new setting |
| UI and workflows intact | met | 223 Dart tests; only status text «Восстанавливается подключение» and the report preview sentence added |
| Backend contracts compatible | met | no API/OpenAPI change; snapshot keys additive |
| Automated tests pass / app builds | met | section 4 |
| Feasible runtime tests | not met | no emulator or phone was available |
| Unverified scenarios labelled | met | section 6 and `RELIABILITY2_DEVICE_TESTS.md` |

## 6. Not verified, not possible overnight
Physical phones, emulators, Doze timing, OEM battery managers, real Wi-Fi/mobile handover, the
TUN replacement during a restart, background service start limits, Lockdown behaviour, IPv6-only
and NAT64 networks, leak tests, battery use. `RELIABILITY2_DEVICE_TESTS.md` lists the phone tests.

## 7. Remaining limitations
| Limitation | Impact | How to verify or resolve |
|---|---|---|
| Background recovery depends on Android allowing a background service start | Without the battery exemption, a window that ends while the app is closed may stay down until the app is opened | Device test D; keep asking for the exemption |
| Process may be killed after the service stops | Recovery then waits for an alarm, boot or app open | Device test D; a persistent foreground service between windows would fix it but costs a permanent notification and battery |
| Restore engine renews from Dart timers (ADR-121) | About one renewal chance per window in deep Doze until the app is opened | Device test B; native renewal for the restore engine is the follow-up |
| Handshake-only health | A node that answers handshakes but drops data is shown as healthy | Server-side egress check, separate issue |
| No leak measurement | IPv6/DNS leak claims are expectations from code | Packet capture on a phone |
| Lockdown | Only Android enforces it; app logic cannot | Documented (ADR-132) |

## 8. Final status
**Build-validated.** Code, unit tests, Dart tests, lint and APK builds pass. Not emulator-tested,
not physically tested, not production-ready.

What you need to do: review the commits on `claude/connection-reliability` (nothing is pushed),
build a signed APK with 900 s windows, and run `RELIABILITY2_DEVICE_TESTS.md` — test A first.
