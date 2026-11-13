# Connection reliability — run 3 report, 2026-11-13

Plain English. **Nothing was pushed. Nothing was deployed. No server, VPS, panel or beta system was
touched. Nothing was tested on a physical phone or an emulator.** Owner files (the untracked
`docs/evidence/i-030-beta-apk-*.json`) were not touched. This report extends
`RELIABILITY2_REPORT.md`, which is kept as it was.

## 1. Worktree and commits
- Path `.claude/worktrees/fixes-before-beta-0eeeba`, branch `claude/connection-reliability`, start HEAD `0835dc4`.
- Code and tests: commit `29e912b` (one commit, see §5 for why it is not split per item).
- Docs (this report, ADRs 133–138, battery inventory, device-test additions): the commit after it.

## 2. Status per item

| Item | Status | Files | Tests added | Still needs a phone |
|---|---|---|---|---|
| **A** health beyond handshake | **done in code and tested**; device proof missing | `DataPathWatch.java` (new), `ConnectionSupervisor.java` (`transport` 7-arg form), `ClientApplication.java` (`snapshot`), `native_tunnel.dart` (`DataPathHealth`), `window_connection.dart` (`_establishedStatusConfirmed`, `dataPathFlowFor`) | `DataPathWatchTest` (8), `DataPathHealth` Dart tests (8 in `data_path_health_test.dart`), `ConnectionSupervisorTest.stalledDataPathIsDegradedAndNeverRequestsRecovery`, `window_connection_test` ADR-133 | A dead node that answers handshakes (device test F) |
| **B** native renewal for the restore engine | **partly**: single-owner rule done and tested; native restore renewal **not** implemented (ADR-136) | `RenewalOwner.java` (new), `ClientActivity.java` (`startNativeRenewal` guard), `ClientApplication.java` (`restoreEngineAlive`) | `RenewalOwnerTest` (3) | Doze behaviour of the 4-minute Dart wake-up (test B, M) |
| **C** background start limits | **done in code and tested** (decision and state); the Russian text exists, but no screen is wired to show it yet | `ConnectionSupervisor.java` (`startRefused`, `REFUSED_RETRY_MS`, category), `ClientApplication.java` (`startRecovery`, snapshot `stopCategory`), `connection.dart` (`messageFor`), `native_tunnel.dart` (`stopCategory`) | 2 supervisor tests (`refusedBackgroundStartKeepsIntentAndRetriesOnTheNextTrigger`, `backgroundRefusalIsTransientNotTerminal`), `background_refusal_test.dart` (2) | Whether Android refuses the start on the owner's device (test K) |
| **D** process killed after stop | **partly**: reconciliation rules tested; an orphaned running tunnel with intent off is **not** stopped automatically (ADR-134) | `ConnectionSupervisor.java` (`restore` rules, unchanged logic) | `processRestartReconcilesPersistedIntentOnTheNextTrigger` | Process kill and reboot behaviour (test L) |
| **E** IPv6 and NAT64 | **partly**: policy values tested; JSON assembly not unit-tested; no leak claim (ADR-137) | `RoutePlan.java` (new), `LibboxConfigFactory.java` (uses `RoutePlan`) | `RoutePlanTest` (6) | Packet capture for IPv6, DNS and Lockdown; IPv6-only and NAT64 networks |
| **F** battery | **done**: inventory written; the 15 s tunnel check no longer runs while no window is up (ADR-135). Not measured | `ClientApplication.java` (`armWatch`, `WATCH_MS`), `docs/overnight/RELIABILITY3_BATTERY.md` | none (Android scheduling) | Battery statistics on a phone (see the battery doc) |
| **G** transport recovery keeps TUN | **static guard only**; device proof **missing** (ADR-138) | `RestartInPlacePatchTest.java` | `successfulRestartDoesNotTearDownBeforeTheNewInterfaceIsUp`, `tunnelUpEstablishesTheInterfaceExactlyOnce` | TUN replacement during a real restart (test A) |
| **H** Wi-Fi to cellular and resume | **unit tests of supervisor decisions**; device proof **missing** (ADR-138) | `ConnectionSupervisorTest.java` | `wifiToCellularAndBackAreRecoveryDecisions` (plus existing `PhysicalNetworkTest`) | Real hand-over and resume (tests A, C, E) |
| **I** documentation | done (this file, ADRs 133–138, battery doc, run 2 report unchanged, device-test additions K–N appended; steps A–J kept) | `docs/overnight/RELIABILITY3_REPORT.md`, `RELIABILITY3_BATTERY.md`, `RELIABILITY2_DEVICE_TESTS.md`, `docs/adr/133`–`138` | — | — |

Run 2's "not met / partly met" rows: rows about handshake-only health, TUN, battery and IPv6 are updated
in §4. The run 2 report itself is unchanged.

## 3. Verification (real results, this computer)

| Check | Command | Result |
|---|---|---|
| Full client gate | `.\scripts\build_flutter_client.ps1 -WindowTunnels -WindowSeconds 900` (ASCII snapshot) | **exit 0** (`GATE_EXIT=0`) |
| Dart format | inside the gate (`dart format --set-exit-if-changed lib test integration_test`) | **64 files, 0 changed** |
| Dart analyze | `flutter analyze --no-pub` inside the gate | **No issues found** (ran in 40.5 s) |
| Dart tests | `flutter test --no-pub` inside the gate | **234 passed, 0 failed** (run 2: 223; +11 new) |
| Dart tests in place (before the gate) | `flutter test --no-pub` in the worktree | 234 passed (same count) |
| Android unit tests | Gradle `:app:testDebugUnitTest` inside the gate | **135 tests, 0 failures, 0 errors, 0 skipped** (run 2: 111; +24 new) |
| Android lint | Gradle `:app:lintDebug` inside the gate | passed (`BUILD SUCCESSFUL`) |
| Debug APK | `flutter build apk --debug` inside the gate | built |
| Release APK | `flutter build apk --release` inside the gate | built (45.1 MB) |
| Python / API | not run | no API file changed |
| Emulator / phone | not run | none available |

Note: `flutter analyze` run directly in the OneDrive path crashed (exit 255, a stack trace); the same
analyze inside the ASCII snapshot passed. The gate is the authoritative result.

Note: the tests were run on the final tree (all items), not on each intermediate state. The
combined commit was therefore not built separately per item.

APKs (local test builds, not signed by me; inside
`%LOCALAPPDATA%\ProjectVpn\i013-builds\a9824b66e4974b6c8369a69f7fb8e63f\apps\client\build\app\outputs\flutter-apk\`,
and copied to the worktree `apps/client/build/app/outputs/flutter-apk/`, which is git-ignored):

| Artifact | SHA-256 |
|---|---|
| `app-debug.apk` | `92946d348c0c4f257ac041495243c7f9180e4832a3215ed2665029c0fab55c38` |
| `app-release.apk` | `a62aac6ac33be6c6468ac583fec219bae12a4d91b51945f794a6fb8dbb789760` |

These APKs are for test on your phone only. Do not distribute them. Sign the release build with your
own script; I did not touch the signing setup.

## 4. Acceptance criteria (run 2 table, updated)

Met = code does it and a test proves the rule. **None of these is proven on a phone.**

| Criterion (run 2 wording) | Run 2 | Run 3 | Proof |
|---|---|---|---|
| Wi-Fi/cellular transitions recover automatically | partly met | partly met | supervisor transition tests (ADR-138); device test A |
| Transport recovery preserves the TUN | partly met | partly met | static guard (ADR-138); device test A |
| Recovery when Android resumes execution | partly met | partly met | refused-start handling (ADR-134); device tests K, L |
| Minimal battery use | partly met | partly met (less work) | 15 s tick removed while disconnected (ADR-135); not measured; battery doc |
| Broken connection not shown healthy forever | partly met | **met in code** | ADR-133: stall → degraded, not connected; device test F |
| No unintended DNS/IPv6 bypass | not verified | **partly: expectations and policy values tested** | ADR-137; no leak test |
| Background recovery depends on Android allowing a background start | limitation | **handled in code** | ADR-134; device test K |
| Process may be killed after the service stops | limitation | **partly** (rules tested) | ADR-134 D; device test L |
| Restore engine renews from Dart timers | limitation | **single owner tested; native renewal not added** | ADR-136; device test B, M |
| Handshake-only health | limitation | **addressed in code** | ADR-133; device test F |

## 5. Decisions and limits
- **One commit for code (`29e912b`).** The items share `ClientApplication.java`,
  `ConnectionSupervisor.java` and their test files. Splitting by item would need hunk-level staging,
  which I did not do without an interactive tool. Each item is still listed separately above.
- **No native renewal for the restore engine** (ADR-136). The only way is a new HTTP path that holds the session
  token in native code, the risk ADR-121 refused. Not implemented, not behind a flag.
- **Orphaned tunnel with intent off is not stopped** (ADR-134). Known limit.
- **JSON of the libbox config is not unit-tested** (ADR-137). `org.json` is an Android stub on the JVM.
- **Stall timing** (120 s, 8 KiB) is a heuristic; no measurement on a real node exists.
- **Headless poll 3 s and 4-minute restore wake** are unchanged until device test B.
- **Keepalive 25 s and rekey 120 s** are unchanged (see the battery doc).

## 6. What you need to do on a phone
Run the tests in `RELIABILITY2_DEVICE_TESTS.md`, starting with A (Wi-Fi to mobile), then the new steps
F, K, L, M, N. Use a signed build with 900 s windows, built by your release script. Record the battery
numbers on a named device and Android version.

## 7. Final status
**Build-validated and unit-tested.** Gate exit 0, analyze clean, 234 Dart tests, 135 Android unit tests,
lint passed, both APKs built. **Not emulator-tested, not physically tested, not production-ready.**
Nothing is pushed or deployed.
