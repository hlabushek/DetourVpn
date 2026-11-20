# ADR-155 — A failed native init is retried instead of lasting until a restart

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Build/unit-test
verified only; not verified on a device.**
**Builds on:** ADR-121/144 (restore engine start), ADR-126 (worker-thread confinement).

## Problem (tester report: cold start, «Повторить» did not help until the app was restarted)
`ClientApplication.onCreate` ran the native init (vault, device key from the Android keystore,
`AwgProviderV1`) once, as a worker task. On any failure it set `reason = "native_unavailable"` and
left `provider == null` for the life of the process. Nothing ever ran it again (code before this
change, `ClientApplication.java:199-214`).

With `provider == null`, every UI channel call other than session/preference storage throws
(`ClientActivity`: `if (!storage && !preference && app.provider == null) throw ...`), so:
`publicKey` fails in `_ensureDevice` → `native_unavailable`, `connectWindow` fails →
`native_engine_unavailable`, `snapshot` fails → `native_state_unknown`. The screen shows
«Требуется действие» / «Повторить», and every tap fails the same way. A restore engine cannot start
either (`startAlwaysOnRecovery` refuses `provider == null`). Only killing the process (restarting the
app) runs the init again — exactly the reported symptom.

The init is most exposed right at a cold start: it is the first keystore use of the process
(`SecureVault.key()` loads `AndroidKeyStore` and decrypts the device key), and the Go engine library
is loaded there. A transient keystore error at that moment was permanent for the process.

## Decision
- The init moves to `initNative()` (worker thread). It keeps what already succeeded (the vault object
  and the device key) and only creates what is missing. It never replaces an existing device key:
  `SecureVault.device()` creates a key only when no key file exists; a failed read throws.
- `ensureNative()` (worker thread): when `provider` is null after a failed init, it runs the init
  again, at most once per `ConnectionSupervisor.NATIVE_INIT_RETRY_MS` (2 s, pure rule
  `nativeInitRetryDue`). It is called before every UI channel call that needs the engine and before a
  restore engine starts. A success clears `native_unavailable` and the `native_init` failure stage.
- A failure is logged as `native_init_failed <stage>` (`vault`, `device_key`, `awg_backend`), codes
  only.

## Alternatives
- Retry in a loop from `onCreate`: rejected, it would spin on a permanent failure (a corrupt key
  file) and use the keystore with nobody waiting.
- Show «перезапустите приложение»: rejected, the retry is safe and cheap.

## Assumptions
- A keystore or engine-load failure at a cold start can be transient; a permanent one now costs one
  init attempt per 2 s while the UI keeps asking, which is bounded and cheap.

## Security and privacy
No new key material, permission, network call or stored data. The device private key stays in the
app-private vault and is never regenerated while a key file exists, so the device identity on the
server does not change because of a retry. The log carries only stage codes.

## Test plan and results
- Java substitute (javac `--release 17` + JUnit 4.13.2, not Gradle): main run **252 passed**; from
  `apps/client/android/app` **9 passed**. New:
  `ConnectionSupervisorTest.aFailedNativeInitIsRetriedAtABoundedPace` (incl. "never failed: no retry"
  and the 2 s bound), `ConnectionWiringTest.aFailedNativeInitIsRetriedBeforeItIsNeeded` (static guard:
  retried before the provider check in `ClientActivity` and in `startAlwaysOnRecovery`; the device
  key is only created when missing).
- The keystore failure itself cannot be reproduced on the JVM; `initNative` wiring is compiled only.
- No Dart change; `flutter test` not re-run for this commit.

## Not verified
No device run, no Gradle build. The cause of the tester's failure is not proven: this removes the one
code path that makes a cold-start native failure last until a restart. The diagnostic report of a
future occurrence should show `native_init_failed <stage>` in the event log.

## Remaining (out of scope, reported)
At a cold start `main.dart` treats a failing `sessionRead` (for example the same keystore error) as an
invalid session and erases it (`restore()` → `forgetSession()`), which signs the user out. Not
changed here; it needs its own decision about which storage errors are transient.
