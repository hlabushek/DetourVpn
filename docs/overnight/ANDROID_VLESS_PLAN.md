# Android VLESS + Reality preparation — plan (2026-11-11)

Status: preparation only. Nothing enabled, nothing committed, nothing run on a device.

## Goal
Make the Android client able to add a VLESS + Reality outbound later (panel servers, ADR-111),
without changing the shipped AmneziaWG beta path. Panel servers still show the Russian
"not supported on Android" text (ADR-113).

## Facts checked in the repo
- Live Android client: `apps/client/android` compiles the shared Java tree
  `apps/android/app/src/main/java` and excludes `LibboxProviderV1.java` and `LibboxVpnService.java`
  (ADR-084). Tunnel = AmneziaWG `GoBackend`.
- Local libbox AARs exist in `.cache/amnezia-box-build` (`libbox-1.260910-arm64.aar`,
  `libbox-1.260910-arm64-x86_64.aar`). They are built from the pinned Amnezia Box source
  (`third_party/amnezia-box/source-lock.json`, `android_tags` = `with_awg, with_gvisor`).
  No `with_utls` tag, so Reality cannot run in them (ADR-112 §4).
- Android Gradle uses `dependencyLocking` and a locally built `PREPARED.json` for the AWG SDK
  (`apps/android/tunnel/build.gradle.kts`). No prebuilt VLESS core is pulled from a remote repo.
- Local JDK: system `java` is 1.8; Android Studio JBR is JDK 21. JUnit 4.13.2 is in the Gradle cache.

## Order of work
1. Write ADR-114 (core choice). Done in this run.
2. Add a pure-Java, versioned adapter (`VlessEngine`, `VlessEngineCapability`,
   `VlessEngineGate`, `NoVlessEngine`) in the shared Java tree. Feature flag
   `VlessEngineGate.FEATURE_ENABLED = false`. No libbox import, no service change.
3. Unit tests for the gate (JUnit 4) in the client test tree.
4. Run the tests: standalone `javac` + JUnit with the JBR 21 (Gradle not run; see report).
5. Owner steps file (`ANDROID_VLESS_OWNER_STEPS.md`).
6. Later issues (NOT in this run): rebuild libbox with `with_utls`; a real `VlessEngine`
   implementation over libbox; wire it to `VpnService` with its own lifecycle; delivery of
   panel servers to Android; device test.

## Dependencies
- Step 2 depends on the ADR decision (step 1).
- Any real engine depends on the owner decision to add `with_utls` to the Android tags and rebuild
  the AAR (also needed for Windows, ADR-112 §4).
- Real use depends on panel server delivery (ADR-112 §5), not done.

## Risks
| Risk | Mitigation |
|---|---|
| Adapter code is compiled into the production APK even though it is off | It is pure Java with no native code; gate returns unavailable; tests pin the default |
| Rebuilding libbox with `with_utls` changes the binary that AWG beta users have | Separate build and hash record; AWG regression check before shipping (owner step) |
| GPL-3.0 engine in the app | Already accepted for the engine (ADR-084 / source-lock); owner must confirm the distribution note |
| VLESS runs in the same process as the AWG GoBackend | Not decided here; a single active tunnel rule is needed before enabling (future ADR) |
| APK size grows | Measure after the real engine is added; not measured now |
| Tests give false confidence | Tests cover only the capability gate; no tunnel, handshake or egress is verified |
