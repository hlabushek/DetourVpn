# Handover — connection survivability plan

**Where we are:** Phases 0 to 6 are **CODE COMPLETE** and committed locally (ADR-144 to
ADR-149), plus the Phase 4 test gaps (`fd7ad24`). **The plan is NOT verified on a device.** There
was no APK build, no Gradle run of any kind, no device or phone run, and no Google Play policy
check. Every result comes from Dart tests, `flutter analyze` on an ASCII copy and the javac +
JUnit substitute for the Android unit tests. "Code complete" does not mean "working".

**Before any build:** Phase 6 changed `apps/android/patches/go-only.patch` and its pinned hash.
The prepared SDK tree in `.cache` (`sdk-go-only-c5-20261113`) is now stale; a build against it
fails the hash check by design. Prepare a fresh tree first (ADR-149 "Owner steps", step 1).

**One-shot owner build:** `scripts/run_overnight_build.ps1` does ADR-149 step 1 end to end. It
prepares `.cache\i-011\sdk-go-only-c6-20261117`, copies `jniLibs` and `assets` from c5, checks the
patch hash and both `libwg-go.so` files, asks for the API origin, keystore path and password
(hidden), then runs `scripts\build_flutter_client.ps1 -ApiOrigin <origin> -WindowTunnels`. At the
end it prints the release APK path and the `adb install -r` command. It stops at the first error.
Not run by the agents; the owner runs it. It has not been executed yet, so its first run is
unverified.

- Branch: `claude/connection-reliability` (worktree `.claude/worktrees/fixes-before-beta-0eeeba`),
  not pushed.
- Phases 0 and 1: commit `1fdbbf4`, ADR-144 (`docs/adr/144-native-connection-state-and-single-owner.md`).
- Phase 2 (initial connect hardening): commit `b5766b1`, ADR-145
  (`docs/adr/145-initial-connect-hardening.md`).
- Phase 4 (network roaming): the commit on top of `b5766b1`, ADR-146
  (`docs/adr/146-network-roaming.md`): 0.5 s action on a new network, 8 s loss confirmation,
  `setUnderlyingNetworks` after each establish, backoff reset on a returning network, two
  restarts per change then a new lease. The former gap (UI-started tunnels had no `protector`, so
  `setUnderlyingNetworks` was skipped) is closed in code by Phase 6.1 (ADR-149); not verified on a
  device.
- Phase 4 test gaps: commit `fd7ad24`. The network-callback decisions moved into the pure
  `NetworkCallbacks` helper (behaviour unchanged) with `NetworkCallbacksTest` (8 s loss timer,
  cancel on `onAvailable`, shorter loss). A TODO at the `restartInPlace` call site asks for a
  device check that `establish()` clears the underlying network and that the order is right.
- Phase 3 (surviving Doze): the commit after `fd7ad24`, ADR-147
  (`docs/adr/147-surviving-doze-overnight.md`):
  - 3.1 server: `MAX_WINDOW_SECONDS` 900 → 3600 (API contract, node gate `MAX_WINDOW_MS`, OpenAPI
    maximum, two server tests edited). **Not deployed and not tested** (pytest was not allowed in
    that task). The client still asks for 900 s; switching it is a later step after the API and
    every node agent are deployed.
  - 3.2 `RENEW_WINDOW` uses `setExactAndAllowWhileIdle` when exact alarms are allowed
    (`SCHEDULE_EXACT_ALARM`), renewing 10 min before expiry; otherwise the inexact fallback keeps
    the earlier ADR-142 margin. TODO: Play policy check by the owner before any Play release.
  - 3.3 `RecoveryCompanionService`, a foreground service only while the VPN is wanted and no
    tunnel is up.
  - 3.4 `BackstopJobService`, a 15-minute JobScheduler job (WorkManager was not added: not a
    dependency, and the Gradle lockfiles cannot be regenerated without Gradle).
  - 3.5 the only new wake lock is the backstop's, bounded to 10 s.
- Phase 5 (dead-peer and silent-drop detection): the commit after `8d46858`, ADR-148
  (`docs/adr/148-dead-peer-and-silent-drop-detection.md`). See the Phase 5 section below.
- Phase 6 (system integration): the commit after `5f1af00`, ADR-149
  (`docs/adr/149-lockdown-boot-and-update-integration.md`). See the Phase 6 section below.
- ADRs of this plan: ADR-144, ADR-145, ADR-146, ADR-147, ADR-148, ADR-149.
- Master plan: `C:\Users\JetGame\.claude\plans\pasted-content-id-2a60-act-as-prancy-nest.md`
  (audit, phases, owner decisions).
- Owner decisions (2026-11-17):
  - longer server window than 900 s: **yes** (Phase 3.1);
  - direct boot: **option (a)**, keep `BOOT_COMPLETED`, rely on Lockdown, document it;
  - automatic node failover on a confirmed dead peer: **yes** (Phase 5.2).
- Order of phases: 0 → 1 → 2 → 4 → 3 → 5 → 6 (all code complete locally; none device-verified).
- Nothing has been verified on a phone. Every client result so far comes from unit tests (Dart
  tests and the javac + JUnit substitute below), not from Gradle or a device. The Phase 3 server
  change has no test run at all.
- Open TODOs (owner-requested, in code and ADRs):
  - health probe URL is still `none` (`DEAD_PEER_PROBE_URL` in the manifest; ADR-148/149): needs a
    real project-owned https health endpoint before the probe can run;
  - the health probe may never run on Android (the app excludes its own package from the VPN;
    Android may refuse the bind, `bind_refused`): needs a device check and a routing decision
    (`TunnelProbeRunner`, manifest, ADR-148/149);
  - restart order after `restartInPlace` / `setUnderlyingNetworks` (ADR-146,
    `ClientApplication.restartTunnel`);
  - Play Store exact-alarm and `systemExempted` policy check (ADR-147, manifest,
    `NativeRenewer.schedule`).

## Tester bug fixes and audit (2026-11-21, autonomous run, committed locally, not pushed)
Every entry is **build/unit-test verified only**: no APK, no Gradle, no device. Java results are
the javac + JUnit substitute below (against the c6 SDK tree), not a Gradle pass.
- **ADR-151** (UI kept «Восстановление связи» ~20 s after the handshake): commit `<pending>`. Native
  state no longer waits 15.5 s for the watchdog to clear a proven restart; native re-checks the state
  each second for 30 s after window start / restart / network change; Dart renders events during a
  poll and the poll's first snapshot before its API call. Tests: Java 249 + 7 passed, `flutter test`
  318 passed, analyze only the `assets/orbit/` warning. Unverified on device: the time to
  «Подключено» after a Wi-Fi switch. (SHA filled in by the next commit: `fe99cc9`.)
- **ADR-152** (rare random drops): commit `b8659c3`. A 5 s timeout of the
  native renewal view (a busy worker, for example the installed-apps list) was read as "tunnel
  stopped" and ended native renewal; the window then expired. Now a timeout retries after 5 s, and
  the `apps` call runs off the tunnel worker. Tests: Java 250 + 7 passed (no Dart change).
  Unverified on device: renewal while the split-apps screen loads.
- **ADR-153** (rare random drops, second cause): commit `0c3fdc3`. The watchdog kept
  the old window's start, network trigger and spent restart budget when a new window started
  within 15 s of a stop, so the new window could be restarted or given up (`NEW_LEASE`) at its
  first tick. `stop()` and `prepareWindow()` now reset it; `beginWindow()` starts it. Tests: Java
  251 + 8 passed (new `ConnectionWiringTest`, run from `apps/client/android/app`). Unverified on
  device.
- **ADR-154** (connect needs retries): commit `612ac1a`. A tap on «Подключить» while
  a poll ran (on return to the app it reconciles, up to one API timeout) was silently ignored. It
  now waits for the poll (shown as busy) and then connects, unless the poll left a connection or a
  pending revoke. Tests: `flutter test` 320 passed, analyze only `assets/orbit/`. Unverified on
  device.
- **ADR-155** (cold start: «Повторить» useless until restart): commit `24acf69`. The
  native init (vault, device key, engine) ran once in `onCreate`; a failure left the engine null for
  the whole process. `ensureNative()` now retries it (at most every 2 s) before UI calls and before
  a restore engine starts; the device key is never replaced. Tests: Java 252 + 9 passed. Unverified
  on device; the tester's actual cause is not proven (look for `native_init_failed` in the event
  log). Open: a failing `sessionRead` at cold start still erases the session (see ADR-155).
- Stage 2 audit:
  - **ADR-156** (protected API relay): commit right after `24acf69`. A relayed connection whose
    client gave up on a black-holed upstream held one of the 8 relay slots until TCP aborted; eight
    blocked every restore-engine API call. The other direction now gets 15 s after the first ends.
    Tests: Java 253 + 9 passed. Unverified on device.

## Rules for the next agent
- One phase at a time, with one ADR per phase in `docs/adr/` (next free number: 152).
- Commit locally with the owner's OK. No push.
- No `flutter build`, no `gradlew assemble`, no release or deploy scripts, no adb or device tests,
  and no server changes without explicit approval.
- Allowed checks: `flutter test`, `flutter analyze` (see below), the Android unit tests (see below).
- Leave the untracked `docs/evidence/i-030-*.json` files and
  `docs/research/wg-tunnel-reconnect-comparison-20261116.md` alone. They are the owner's
  material.

## Known environment issues
- **Flutter is not on PATH.**
  - SDK: `%LOCALAPPDATA%\ProjectVpn\toolchains\flutter\bin\flutter.bat`.
  - Set `PUB_CACHE=%LOCALAPPDATA%\ProjectVpn\toolchains\pub-cache`.
  - JDK 21: `C:\Program Files\Android\Android Studio\jbr`. Android SDK 36:
    `%LOCALAPPDATA%\Android\Sdk`.
- **`flutter analyze` crashes on the Cyrillic path** (`OneDrive\Документы`, exit 255). Copy
  `apps/client/{lib,test,pubspec.yaml,pubspec.lock,analysis_options.yaml}` to an ASCII-only folder
  (for example the session scratchpad) and run it there. The only expected finding is the
  `assets/orbit/` warning, because that folder is not copied. Do not change code to work around
  the path.
- `flutter test` works in place: 315 tests passed at the Phase 5 and Phase 6 commits (310 before).
- **No Gradle wrapper in `apps/client/android` — on purpose, not generated.**
  - `scripts/build_flutter_client.ps1` builds from a temporary ASCII copy. It puts a wrapper there
    from `apps/android/gradle/wrapper/gradle-wrapper.jar` (SHA-256 pinned in the script) and
    `apps/client/gradle-wrapper.properties` (Gradle 8.14.3, distribution SHA pinned).
  - Adding a wrapper in place would not make `gradlew testDebugUnitTest` usable here, for two
    reasons:
    - the Flutter Gradle plugin needs the ASCII path;
    - `settings.gradle.kts` includes `:tunnel`, which builds the native amneziawg-go library.
      That is a full native build, which is outside the allowed checks.
  - Making Gradle unit tests runnable needs its own small task: a test-only script that copies to
    an ASCII path and runs `testDebugUnitTest` with the prebuilt tunnel. It needs the owner's OK.
- **Substitute for the Android unit tests** (used for ADR-140 to ADR-146; report it as a deviation,
  not a Gradle pass):
  1. Compile with `javac --release 17`:
     - sources: every `apps/client/android/app/src/main/java/**/*.java`, except the generated
       `GeneratedPluginRegistrant.java`, which is replaced by an empty stub;
     - plus `apps/android/app/src/main/java/**`, except `MainActivity`, `PocApplication`,
       `LibboxProviderV1` and `LibboxVpnService` (the same exclusions as Gradle);
     - plus the patched tunnel sources. Up to Phase 5 that was
       `.cache/i-011/sdk-go-only-c5-20261113/tunnel/src/main/java/**`. Since Phase 6 the patch
       differs from `c5`, so use a tree with the current patch applied (Phase 6 used a scratch
       copy: `git archive` of the pinned SDK revision from `.cache/i-011/awg-android`, then
       `git apply` of `go-only.patch`, outside the repository);
     - classpath:
       - `android-36/android.jar`;
       - `flutter_embedding_debug-1.0.0-*.jar`;
       - `annotation-jvm-1.9.1`;
       - `jsr305-2.0.1`;
       - `collection-1.1.0`;
       - `lifecycle-common-2.7.0`;
       - the `lifecycle-runtime-2.7.0` `classes.jar` (all from `~/.gradle/caches`).
  2. Run all tests in both `src/test/java` trees with JUnit 4.13.2 and hamcrest-core 1.3.
  3. Run `RestartInPlacePatchTest` and (since Phase 6) `SystemIntegrationWiringTest` from
     `apps/client/android/app`, because they read files relative to it.
  4. Expected result at the Phase 5 commit: 238 tests pass (235 in the main run plus 3
     `RestartInPlacePatchTest`). It was 194 at the Phase 4 commit, 202 after `fd7ad24` and 209
     at the Phase 3 commit. At the Phase 6 commit: 253 (246 in the main run plus 7 from
     `apps/client/android/app`: 3 `RestartInPlacePatchTest` + 4 `SystemIntegrationWiringTest`).
  5. The `find` results under Git Bash are POSIX paths; convert each jar with `cygpath -w`
     before joining the `;`-separated classpath for the Windows `javac.exe`.

## Phase 3 — surviving Doze overnight (recorded in ADR-147; committed locally)
What was run for it: the Java substitute (209 passed), `flutter test` (310 passed, no Dart
change) and `flutter analyze` on an ASCII copy (only the expected `assets/orbit/` warning).
What was **not** run or verified:
- no device run, no Doze or OEM-killer scenario;
- no Gradle build, manifest merge or lint;
- no server test run (pytest) and no deploy of API or node agents;
- no Play policy check for `SCHEDULE_EXACT_ALARM` / `USE_EXACT_ALARM` or the `systemExempted`
  foreground-service type.
Open follow-ups, each needing the owner's OK:
- run the server tests, then deploy the API and every node agent;
- only after that, let the client request windows longer than 900 s.
See ADR-147 "Known limitations" and "Owner steps".

## Phase 5 — dead-peer and silent-drop detection (recorded in ADR-148; committed locally)
- 5.1 `DataPathWatch` is now owned by the pure `DeadPeerWatch` and fed only from the native watch
  tick; `snapshot()` only reads it. The Dart `DataPathHealth` tracker stays: on Android it is never
  fed (native state is rendered first; a Dart test pins this), and it is still Windows' only rule.
- 5.2 Suspected stall (60 s outbound without inbound, or 180 s without a handshake) → one HTTPS
  probe through the VPN network (5 s). Pass = healthy. Two failures → restart in place, then a new
  lease (stop reason `dead_peer`), then failover only when opted in, then exhausted (the watchdog
  takes stalls back). `UNAVAILABLE` never escalates.
  - Health URL placeholder: `AndroidManifest.xml` meta-data
    `org.example.vpnpoc.DEAD_PEER_PROBE_URL` = `none` (probe off). No real endpoint anywhere.
  - Failover opt-in: meta-data `org.example.vpnpoc.DEAD_PEER_FAILOVER` = `false` (default off).
    When on, the restore engine uses the first other available AmneziaWG node for one window
    (`failoverRead`, `failoverNode`); the saved node is unchanged.
- 5.3 A captive portal (CAPTIVE_PORTAL, not VALIDATED) shows `RECOVERING{captive_portal}` with a
  sign-in hint; nothing restarts while it blocks traffic (the network is not usable).
What was run for it: the Java substitute (238 passed: 235 + 3), `flutter test` (315 passed) and
`flutter analyze` on an ASCII copy (only the expected `assets/orbit/` warning).
What was **not** run or verified:
- no device run; no silent-drop, captive-portal or failover scenario;
- **the probe may be unable to run on a phone at all**: the app excludes its own package from its
  VPN (ADR-132), so binding a socket to the VPN network is probably refused, which the code reports
  as `UNAVAILABLE` (inert, not harmful). A device run must check this first;
- no Gradle build, manifest merge or lint (the new meta-data was not checked by aapt);
- no real network probe behaviour (DNS, TLS, timeout) and no Play policy check.
See ADR-148 "Known limitations" and "Owner steps".

## Phase 6 — system integration (recorded in ADR-149; committed locally; CODE COMPLETE, NOT verified)
- 6.1 Lockdown is read only after `establish()` with the `AlwaysOnStatus` rule
  (`lockdownAfterRead`; read in `beginWindow()` and on the watch tick; the pre-establish read in
  `startAlwaysOnRecovery` is gone, R6). GoBackend now reports every service instance
  (`serviceCreated`, a `go-only.patch` change with a new pinned hash), so UI-started tunnels get a
  `protector` (`ServiceProtector`): native renewal under Lockdown and `setUnderlyingNetworks` work
  for them in code. The protector's uses are unchanged (ADR-140/141/146).
- 6.2 Option (a): `BOOT_COMPLETED` kept; not direct-boot aware; no device-protected token. Without
  Lockdown, traffic before the first unlock is unprotected (documented trade-off).
- 6.3 `MY_PACKAGE_REPLACED` added to `BootReceiver`, through the same pure gate as boot
  (`RestoreTrigger`): restores only when the user left the VPN connected and consent is ours.
What was run for it: the Java substitute against a scratch SDK tree with the new patch (253
passed: 246 + 7), `flutter test` (315 passed, no Dart change) and `flutter analyze` on an ASCII
copy (only the expected `assets/orbit/` warning).
What was **not** run or verified:
- no APK, no Gradle (no manifest merge, lint or `testDebugUnitTest`), no device run;
- no Lockdown, protector, reboot or app-update scenario on a phone;
- no Play policy check;
- the shared `.cache` prepared SDK tree was not regenerated (owner step, ADR-149).
