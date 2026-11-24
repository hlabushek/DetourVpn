# Android reliability hardening — verification receipt

Date: 2026-11-12. Baseline checkpoint: `9b0bbc0` (clean tree, explicit empty checkpoint).
Scope: I-030 Android connection reliability. [ADR-092](adr/092-android-connection-supervision.md)
records the architecture and tradeoffs. The follow-up request authorized builds and
feasible device checks; production servers/accounts were not touched.

## Implementation and findings

- Upstream AWG owns encryption, packet authentication and TUN. No cryptography,
  API endpoints, quota amounts, ACK/idempotency/settlement contracts or UI layout
  were rewritten. The missing product-specific lifecycle adapter was rewritten.
- Pinned AWG sockets are **unconnected** UDP sockets. Verified before/after actual
  sends. A duplicated descriptor refers to the same socket and can be rebound
  with Android Network.bindSocket. Cached endpoint source/interface (PKTINFO)
  must not survive handover: the adapter reparses the current roaming destination.
  Native sends are fenced while both sockets are protected/bound and underlying
  networks updated. Binding failure leaves traffic fenced, with bounded retry.
- The app UID is excluded from the VPN. A synthetic DNS probe bound to the VPN
  Network cannot be assumed to work for that UID. Production health now observes
  authenticated payloads delivered by upstream AWG to TUN. Health expires after
  60 seconds and is cleared on network change. Quiet tunnels are unconfirmed,
  not automatically restarted. This does not independently prove Internet/DNS egress.
- One application-owned Flutter engine, API session and renewal controller survive
  Activity destruction/recreation. The context-only bridge retains no Activity.
  Android owns network callbacks and scheduled renewal pulses. No widget timer
  is responsible for keeping authorization alive.
- A CLOCK_BOOTTIME gate checks the current authorization on encrypted Send and
  decrypted TUN Write. It starts closed. Expired/cancelled handles cannot revive.
  Delayed Java/Dart timers cannot extend permission to send. Packets already in
  a syscall cannot be retracted. Deep Doze may still suspend renewal: after expiry,
  traffic fails closed until reconciliation and new admission after wake.
- JNI calls are serialized. Channel responses time out after 15 seconds, cancel
  traffic/intent immediately, and ignore late completion. Further native operations
  require process restart after this fault; a stuck native thread is not killed or
  replaced by another core. False intent is persisted without queueing behind JNI;
  stale queued true intent is rejected by generation.
- Service teardown cancels immediately and moves blocking core shutdown off the
  Android main thread. Epoch checks reject stale cleanup. Foreground ownership
  survives task removal; notification updates use the existing foreground-service notification path.
  Always-on release gating and Android force-stop semantics remain unchanged.

## Changed files and responsibilities

Paths below are repository-relative.

| Files | Responsibility |
| --- | --- |
| `apps/android/reliability/reliability.go` | Native deadline/route/cancellation gate, fresh endpoints, delivered-payload health |
| `apps/android/reliability/*_test.go` | Deadline, cancellation, race, socket contract and real local encrypted path tests |
| `apps/android/patches/reliability.patch` | JNI exports, non-reused handles, socket binding and GoBackend/service lifecycle integration |
| `apps/android/app/src/main/java/org/example/vpnpoc/{AwgProviderV1,TunnelProviderV1}.java` | Versioned adapter capabilities and native hooks |
| `apps/client/android/app/src/main/java/org/example/vpnpoc/ConnectionSupervisor.java` | Generations, offline waiting, health expiry and capped jittered retry policy |
| `AndroidConnectionRuntime.java` in the same directory | Physical-network observation, serialized transport repair and native pulses |
| `TunnelHealthProbe.java` in the same directory | Delivered-data age criterion, no DNS/socket probe |
| `TunnelProbePacket.java` and its JVM test | Standalone packet parser retained from initial rewrite; not used for production health |
| `ClientApplication.java`, `ClientBridge.java`, `ClientActivity.java`, `BoundedMethodResult.java` | Application ownership, method dispatch, foreground state, cancellation and bounded replies |
| `apps/client/lib/{client_lifetime,main,always_on_recovery,native_tunnel,window_connection}.dart` | Retained session/controller, native pulse integration and compatible optional snapshot fields |
| `apps/client/test/{native_tunnel,window_connection}_test.dart` | Channel/controller regressions |
| `apps/client/android/app/src/test/java/org/example/vpnpoc/ConnectionSupervisorTest.java` | Deterministic policy tests, including idle without retry escalation |
| `apps/client/android/app/src/androidTest/java/org/example/vpnpoc/ReliabilityDeviceTests.java` | Isolated real JNI/Activity and blocked-worker cancellation instrumentation |
| Client manifest / app Gradle | Task-independent service, optional test runner, installed NDK pin |
| `scripts/prepare_android_poc.py`, `apps/android/tunnel/build.gradle.kts`, `scripts/build_flutter_client.ps1` | Aggregate reviewed patch/source fingerprint, fresh prepared tree, source verification, CRLF-safe pin parsing |
| `scripts/test_android_reliability.py` | Standalone JVM test runner |
| ADR-092 and this receipt | Design decisions, validation boundary and reproduction |

## Reproduction

Local toolchains: Flutter 3.47.7 / Dart 3.13.5, Android API 36, Java 17 language
level (Android Studio JBR), Gradle 8.14.3 / AGP 8.13.0; native Go 1.25.14 with
reviewed CLOCK_BOOTTIME patch, NDK r26b. App plugin NDK: 28.2.13676358.
No dependency upgrades or release signing credentials were introduced.

```powershell
.venv\Scripts\python.exe scripts/prepare_android_poc.py --target-dir .cache/i-011/sdk-reliability-hardened-v2
# In WSL, set VPN_ANDROID_SDK_ROOT to this prepared tree, then:
# bash scripts/build_android_native.sh
./scripts/build_flutter_client.ps1 -WindowTunnels -TargetPlatforms 'android-arm64,android-x64'
.venv\Scripts\python.exe scripts/test_android_reliability.py `
  --java-home 'C:\Program Files\Android\Android Studio\jbr' `
  --junit .cache/i-011/gradle-8.13/lib/junit-4.13.2.jar `
  --hamcrest .cache/i-011/gradle-8.13/lib/hamcrest-core-1.3.jar
.venv\Scripts\ruff.exe check scripts/prepare_android_poc.py scripts/test_android_reliability.py
.venv\Scripts\python.exe -m mypy scripts/prepare_android_poc.py scripts/test_android_reliability.py
```

Native race test reproduction: copy the prepared native `go.mod`/`go.sum` and
tracked `apps/android/reliability/*.go` into a fresh local test directory and run
`CGO_ENABLED=1 go test -race -v ./...` on Linux/WSL with the pinned Go executable.
These tests use local UDP peers and TEST_ONLY memory TUNs, not an Android VPN.

## Results and acceptance

| Verification | Actual result |
| --- | --- |
| Flutter formatting / analysis / tests | PASS; 118 tests |
| Gradle `:app:testDebugUnitTest` | PASS; 48 tests, zero failures/errors |
| Standalone JVM policy/deadline/parser runner | PASS; 19 tests (subset of Gradle suite, not additional independent coverage) |
| Native `go test -race -v ./...` | PASS; 7 tests including real local AWG encryption/UDP/TUN delivery and timer-free expiry |
| ARM64 + x86-64 native build | PASS; module verification passed |
| Debug + AndroidTest + release APK assembly | PASS; release unsigned |
| Android lint | PASS; zero errors, five warnings (dependency versions and a static ABI warning); both ABIs independently verified inside APK |
| Ruff format/lint, mypy | PASS |
| Android API-35 x86-64 emulator | PASS; 12 instrumentation assertions, overall `passed=true`, `vpn_or_physical_acceptance=false` |

Emulator checks: native library loading, invalid handles rejected, missing data not
health, Activity finish/relaunch and recreation reuse engine, UI does not start a
core, intentionally blocked worker, 15-second response bound, false intent saved
without worker, cancellation and late-response rejection. The disposable AVD had
no test account or backend configuration. The first harness attempt raced Application
onCreate and was fixed with waitForIdleSync before initialization assertions.
The initial lint failure was fixed by using the foreground-service notification
update path; no lint baseline/suppression or security guard bypass was added.

The initial ordinary debug APK was installed only into the disposable emulator.
Final instrumentation uses `com.detourvpn.client.i028` with explicit Gradle flags;
no existing physical-phone app/data was changed. No VPN peer was configured there.

Run the instrumentation from the build snapshot's `apps/client/android` directory:

```powershell
./gradlew.bat --no-daemon -Ptarget=lib/main.dart '-Ptarget-platform=android-arm64,android-x64' `
  -Pi028IsolatedPackage=true -PreliabilityInstrumentation=true `
  :app:assembleDebug :app:assembleDebugAndroidTest :app:testDebugUnitTest :app:lintDebug
# Install the resulting debug and androidTest/debug APKs into the chosen disposable emulator.
adb -s <emulator-serial> shell am instrument -w `
  com.detourvpn.client.i028.test/org.example.vpnpoc.ReliabilityDeviceTests
```

The runner deliberately injects a terminal native timeout; restart the isolated
app process after running it. It never creates an authorized VPN or contacts an API.

| Major scenario | Status and evidence boundary |
| --- | --- |
| Native expiry with no timer callback | PASS in local real encrypted-path test; physical Doze packet capture UNVERIFIED |
| Cancellation / stale session / stale network | PASS unit/race tests; blocked-worker/late-result behavior PASS emulator |
| Activity finish/relaunch/recreation | PASS emulator engine ownership; ongoing VPN under Recents/lock UNVERIFIED |
| Offline waiting, jitter, rapid network changes, idle | PASS policy tests; actual radio handover and captive portal UNVERIFIED |
| Native sockets unconnected / stale source discard | PASS real local socket contract and adapter test; Android radio rebind UNVERIFIED |
| Authenticated delivered-payload health | PASS local AWG test; Android excluded-UID end-to-end egress UNVERIFIED |
| Terminal errors / explicit disconnect | PASS policy and controller regressions; notification/revoke with live VPN UNVERIFIED |
| Foreground service lifecycle | Compiles/lints; active VPN Home/Recents/system-stop behavior UNVERIFIED |
| Screen-off / deep Doze / overnight | UNVERIFIED; authorization deliberately expires safely if renewal cannot run |
| Restart / Always-on / OEM kill | UNVERIFIED; release Always-on remains disabled |
| DNS/IPv4/IPv6/split routing/Lockdown | UNVERIFIED on real traffic; original product policy retained |
| Real WAN VPN connection | UNVERIFIED; no physical connection claimed |

Build snapshot: `C:/Users/JetGame/AppData/Local/ProjectVpn/i013-builds/25c7f28c5ec5481e80289f06b0d2be21`.
Current source Java/manifest/instrumentation fixes were copied into this snapshot
before its final Gradle verification. Dart production files match the 118-test run.
Prepared SDK: `.cache/i-011/sdk-reliability-hardened-v2`; aggregate fingerprint
`0915c948636fce8888702c8d65874b6842e2ecd5258c07a0d10f706f1f6d3255`.

Native unstripped SHA256:
- ARM64: `f8546ad7302b24f67efa75cdb36a99e531306e2f55fb99abb4e42f3714176191`
- x86-64: `9f9910fd5b33114b29f7111b323601f4e8cde2e7a929480eb563ca278e1789d1`

Verified original native binaries equal snapshot/Gradle merged inputs; the APK
entries equal Gradle's stripped outputs. Stripping legitimately changes their hashes.


Physical acceptance requires an approved test account/peer and a real phone.
No such account or physical device was used. Run Home/Recents/recreation, screen-off,
Doze/overnight, Wi-Fi/mobile/oscillation, captive portal/airplane+Wi-Fi, endpoint
outage, revoke/another VPN, Lockdown on/off, DNS/IPv4/split modes and IPv6-only/NAT64.
For each capture model/API/core/network, sanitized state changes, authorization
boundaries, resource counts and independent egress/leak observations. Do not
infer those outcomes from the local tests or APK compilation.

A normal Android force-stop is respected. Always-on release support remains gated.
Without Android Lockdown, OS VPN removal can permit direct fallback; no universal
leak-prevention claim is made. No production readiness or physical acceptance is
claimed by this work.

## Final artifacts and readiness

Artifacts under `apps/client/build/reliability/` (ignored build output):

| Artifact | SHA256 / purpose |
| --- | --- |
| `detour-reliability-debug.apk` | `3553c65065a348f5708241ded82e897af9afbbedaf55537b7da516a7228afd67`; signed, installable, isolated `.i028` package |
| `detour-reliability-release-unsigned.apk` | `367e70ce793346c44d3b4a626b58266c2575da9033aa7ee44c9e4ecfdcc4e396`; ordinary application ID, unsigned, not installable as delivered |
| `detour-reliability-tests.apk` | `394fc3ddc556b2b91df12f87d07b3afe60e6ef3742640e63934e72a61db6bc92`; instrumentation only |
| `receipt.json` | Hashes, snapshot path, 21 matching changed source files and unchanged pub lockfile |
| `reliability-verification.log`, `reliability-release.log`, `reliability-emulator-results.txt` | Successful Gradle, release and emulator evidence |
| `reliability-final-build.log` | Initial 118-test/analyzer run followed by the now-corrected notification lint failure; retained for audit, not the final Gradle outcome |

Release command in the verified snapshot: `flutter build apk --release --target
lib/main.dart --target-platform android-arm64,android-x64
--dart-define=VPN_WINDOW_TUNNELS=true
--dart-define=VPN_WINDOW_BUDGET_BYTES=16777216`. It completed successfully (43.8 MB).
`apksigner verify` passes for debug; release correctly fails verification because
no release key was supplied. No signing key/password was requested or introduced.

Neither APK embeds an API origin: it shows the explicit setup screen. For physical
VPN validation, produce a configured test build with the existing builder's
`-ApiOrigin <approved-staging-origin>` option and an approved test account/peer.
Do not infer a working VPN from the setup-only emulator checks. The disposable
API-35 emulator was stopped after verification.

Assessment: **build-validated and emulator-smoke-validated; not device-accepted or
production-ready**. Next acceptance work is the bounded physical matrix above,
especially handover, foreground/Doze lifecycle and packet-level expiry/leak checks.
The native timeout path deliberately requires process restart; deep Doze does not
guarantee uninterrupted renewal. No production deployment took place. The baseline
checkpoint is committed; implementation changes remain uncommitted for review.
