# Handover — connection survivability plan

**Where we are:** Phases 0, 1, 2 and 4 are committed locally, plus the Phase 4 test gaps
(`fd7ad24`), and the Phase 3 result is recorded in ADR-147 and committed locally. **Next is
Phase 5, which has not been started.** Wait for the owner's go-ahead before starting it.

- Branch: `claude/connection-reliability` (worktree `.claude/worktrees/fixes-before-beta-0eeeba`),
  not pushed.
- Phases 0 and 1: commit `1fdbbf4`, ADR-144 (`docs/adr/144-native-connection-state-and-single-owner.md`).
- Phase 2 (initial connect hardening): commit `b5766b1`, ADR-145
  (`docs/adr/145-initial-connect-hardening.md`).
- Phase 4 (network roaming): the commit on top of `b5766b1`, ADR-146
  (`docs/adr/146-network-roaming.md`): 0.5 s action on a new network, 8 s loss confirmation,
  `setUnderlyingNetworks` after each establish, backoff reset on a returning network, two
  restarts per change then a new lease. Known gap: `setUnderlyingNetworks` runs only when native
  holds the service (`protector`: Always-on, boot, sticky or native recovery starts); UI-started
  tunnels skip it until Phase 6.1.
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
- Master plan: `C:\Users\JetGame\.claude\plans\pasted-content-id-2a60-act-as-prancy-nest.md`
  (audit, phases, owner decisions).
- Owner decisions (2026-11-17):
  - longer server window than 900 s: **yes** (Phase 3.1);
  - direct boot: **option (a)**, keep `BOOT_COMPLETED`, rely on Lockdown, document it;
  - automatic node failover on a confirmed dead peer: **yes** (Phase 5.2).
- Order of phases: 0 → 1 → 2 → 4 → 3 (done locally) → 5 (next) → 6.
- Nothing has been verified on a phone. Every client result so far comes from unit tests (Dart
  tests and the javac + JUnit substitute below), not from Gradle or a device. The Phase 3 server
  change has no test run at all.

## Rules for the next agent
- One phase at a time, with one ADR per phase in `docs/adr/` (next free number: 148).
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
- `flutter test` works in place: 310 tests passed at the Phase 4 commit (no Dart change since
  ADR-145).
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
     - plus `.cache/i-011/sdk-go-only-c5-20261113/tunnel/src/main/java/**`;
     - classpath:
       - `android-36/android.jar`;
       - `flutter_embedding_debug-1.0.0-*.jar`;
       - `annotation-jvm-1.9.1`;
       - `jsr305-2.0.1`;
       - `collection-1.1.0`;
       - `lifecycle-common-2.7.0`;
       - the `lifecycle-runtime-2.7.0` `classes.jar` (all from `~/.gradle/caches`).
  2. Run all tests in both `src/test/java` trees with JUnit 4.13.2 and hamcrest-core 1.3.
  3. Run `RestartInPlacePatchTest` from `apps/client/android/app`, because it reads
     `../../../android/patches/go-only.patch`.
  4. Expected result at the Phase 3 commit: 209 tests pass (206 in the main run plus 3
     `RestartInPlacePatchTest`). It was 194 at the Phase 4 commit and 202 after `fd7ad24`.
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

## Phase 5 — dead-peer and silent-drop detection (next; not started)
From the master plan, section "Phase 5" (5.1 data-path watch from the native tick only, 5.2
active probe through the tunnel and escalation up to node failover — owner decision: yes,
opt-in per ADR, 5.3 captive portal). One ADR (148). Wait for the owner's go-ahead.
