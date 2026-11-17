# Handover — connection survivability plan

**Where we are:** Phases 0 and 1 are done and committed. **Next is Phase 2, which has not been
started.** Wait for the owner's go-ahead before starting it.

- Branch: `claude/connection-reliability` (worktree `.claude/worktrees/fixes-before-beta-0eeeba`),
  not pushed.
- Phases 0 and 1: commit `1fdbbf4` — native `ConnectionState`, connection event log, EventChannel
  `project.vpn/state.v1`, Flutter renders native state (ADR-144). This handover file is committed
  on top of it.
- Master plan: `C:\Users\JetGame\.claude\plans\pasted-content-id-2a60-act-as-prancy-nest.md`
  (audit, phases, owner decisions). ADR for Phases 0 and 1:
  `docs/adr/144-native-connection-state-and-single-owner.md`.
- Owner decisions (2026-11-17):
  - longer server window than 900 s: **yes** (Phase 3.1);
  - direct boot: **option (a)**, keep `BOOT_COMPLETED`, rely on Lockdown, document it;
  - automatic node failover on a confirmed dead peer: **yes** (Phase 5.2).
- Order of phases: 0 → 1 (done) → 2 → 4 → 3 (3.1 server, 3.2–3.4 client) → 5 → 6.
- Nothing has been verified on a phone. Every result so far comes from unit tests.

## Rules for the next agent
- One phase at a time, with one ADR per phase in `docs/adr/` (next free number: 145).
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
- `flutter test` works in place: 295 tests passed at `1fdbbf4`.
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
- **Substitute for the Android unit tests** (used for ADR-140 to ADR-144; report it as a deviation,
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
  4. Expected result at `1fdbbf4`: 185 tests pass.

## Phase 2 — initial connect hardening (next; not started)
Main file: `apps/client/lib/window_connection.dart` (`_connect`, `_reconcile`, `_cleanup`, `_refresh`).
Native side: `apps/client/android/app/src/main/java/org/example/vpnpoc/ClientActivity.java`.
Fake natives and APIs for tests: `apps/client/test/window_connection_test.dart`.

1. **2.1 Permissions first.**
   - Ask for VPN consent (`native.permission()`), and for `POST_NOTIFICATIONS` on Android 13+,
     **before** any API call in `_connect`. Today consent comes after device registration and
     reconcile.
   - The manifest has no `POST_NOTIFICATIONS` yet.
   - Ask for the battery exemption once, with an explanation, at the first connect. Today it is
     asked after a successful connect, at most twice (`ClientActivity.askBatteryExemption`).
2. **2.2 A failed `snapshot()` in `_reconcile` means "unknown".** Retry it and never call
   `native.disconnect()` because of it. Today it becomes `live = null`, which is treated as "not
   adoptable" and leads to a disconnect.
3. **2.3 Keep the user's intent when nodes are unavailable.**
   - If `refreshNodes` fails at startup or the saved node is temporarily missing, do not end the
     intent.
   - Make `window_node_required` retryable after the next node refresh, and do not call
     `_endUserIntent()` for it.
   - Native also maps it to the terminal category `invalid_config`
     (`ConnectionSupervisor.category`); review that mapping.
4. **2.4 New code `handshake_not_established`.**
   - Raise it when there is no handshake within 20 s of `connectWindow`.
   - Retry once on a fresh lease, then fail with a clear Russian message.
   - Today this ends as `lease_expired` with a misleading battery hint.
   - Native already reports `connecting/awaiting_handshake` (30 s) and then
     `recovering/no_handshake` (ADR-144). Reconcile the two thresholds in the ADR.
5. **2.5 Revoke pending.**
   - While `remotePending` is set, show a visible «Повторить» / «Сбросить» action. Today there is
     no action (`PrimaryAction.none`).
   - Stop calling `native.disconnect()` on every poll (`_cleanup` runs from `_refresh` while the
     revoke is pending).
   - Do not let an earlier error hide the revoke state (`error ??= 'revoke_unconfirmed'`).

Done means: Dart tests for each item, the Java substitute run if native changes, ADR-145, and a
local commit after the owner's OK. Do not claim anything is verified on a device.
