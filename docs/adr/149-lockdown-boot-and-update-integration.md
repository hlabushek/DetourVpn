# ADR-149 — Lockdown, boot and app-update integration (connection-survivability Phase 6)

**Status:** implemented on branch `claude/connection-reliability` as Phase 6 of the owner-approved
connection-survivability plan; committed locally only. **Not verified on a device, not built with
Gradle. The GoBackend patch changed, so a fresh prepared SDK tree is required before any build
(see "Owner steps").**
**Builds on:** ADR-098 (boot restore), ADR-121 (restore engine), ADR-132 (the app excludes itself
from its VPN), ADR-139/141 (Always-on status, read only while up), ADR-140 (native renewal under
Lockdown over protected sockets), ADR-144 (single owner), ADR-146 (`setUnderlyingNetworks`),
ADR-147 (Doze aids), ADR-148 (dead-peer probe).
**Owner decision (2026-11-17), Decision 2:** direct boot — **option (a)**: keep `BOOT_COMPLETED`,
rely on Lockdown to block traffic until unlock, document it. No `directBootAware`.

## Problem

- **R6.** `startAlwaysOnRecovery` read `isLockdownEnabled()` before `establish()`. Android answers
  `false` there whatever the setting is (the same platform rule ADR-141 documents for
  `isAlwaysOn()`), so `lockdown` was always `false`. Under Lockdown native renewal then chose the
  direct path, which the OS blocks.
- **No protector for UI-started tunnels.** GoBackend reported its service instance only for
  Always-on, sticky and restore starts. For a tunnel started from the app, `protector` stayed
  `null`: under Lockdown native renewal was refused (`Transport.NONE`), `setUnderlyingNetworks`
  was skipped (`underlying_skipped no_service`, the ADR-146 known gap), and the Always-on status
  was never read for such a tunnel.
- **App update.** After an update Android kills the process and its tunnel. Only `BOOT_COMPLETED`
  restored the user's intent, so a user who left the VPN connected stayed disconnected after an
  update until they opened the app (unless system Always-on restarted it).

## Decision

### 6.1 Lockdown after `establish()`, and a protector for every service instance
- `lockdown` is now set only through `AlwaysOnStatus.lockdownAfterRead(reading, previous)`, the
  same rule as the displayed Always-on status: a reading taken while this app's VPN is not
  established is `null` and keeps the previous value; it can never reset Lockdown to `false`.
- `observeAlwaysOn` updates `lockdown` before its "display value unchanged" early return.
- `beginWindow()` (UI and restore paths, right after `provider.connect` returned, i.e. after
  `establish()`) calls `observeAlwaysOn(protector, tunnelUpNow())`. Dart's `startNativeRenewal`
  for that window runs later on the same worker queue, so it sees the fresh value. The 15 s watch
  tick keeps reading it as before.
- The pre-`establish()` read in `startAlwaysOnRecovery` was removed.
- GoBackend patch (`apps/android/patches/go-only.patch`): `AlwaysOnCallback` gains
  `serviceCreated(VpnService)`, called from `VpnService.onCreate()` for **every** instance,
  before the service future completes (so before `establish()` can run on it). `ClientApplication`
  stores it through the pure `ServiceProtector.created/stopped` rule: the newest live instance is
  the protector; a late stop of an older instance never clears a newer one; `alwaysOnStopped`
  (every `onDestroy`) clears it. Both callbacks run on the main thread, so they do not reorder.
- The pinned patch hash in `apps/android/tunnel/build.gradle.kts` was updated
  (`d84f22bb…ac326c`). A static test checks the pin against the patch.
- Effect: a UI-started tunnel under Lockdown can now renew natively over a protected socket
  (ADR-140 path), and gets `setUnderlyingNetworks` after each establish (closes the ADR-146 gap).

### 6.2 Direct boot — option (a), as decided by the owner
- `BOOT_COMPLETED` is kept. The app is **not** `directBootAware`; there is no
  `LOCKED_BOOT_COMPLETED` receiver and no device-protected copy of any token or session.
- Consequence: after a reboot the VPN is restored only after the user's first unlock (that is when
  `BOOT_COMPLETED` is delivered and when credential-encrypted storage, which holds the vault and
  session, becomes readable).
- Between boot and first unlock:
  - with system Always-on **and** "Block connections without VPN" (Lockdown), Android blocks all
    traffic of other apps until a VPN is up, so nothing leaks; but the device also has no network
    until unlock (incoming messages, for example, wait);
  - without Lockdown, traffic before the first unlock leaves **unprotected** through the physical
    network. This is the accepted trade-off of option (a) and must be stated in user-facing help
    when it is written.
- Security trade-off, explicitly accepted: option (b) (direct boot plus a device-protected renewal
  token) would protect that window without Lockdown, but it would keep a usable credential in
  storage that is readable before the user authenticates, which a seized or stolen locked device
  could expose. Option (a) keeps every credential in credential-encrypted storage only.

### 6.3 `MY_PACKAGE_REPLACED`
- The existing exported `BootReceiver` now also filters `android.intent.action.MY_PACKAGE_REPLACED`.
  Both actions are protected broadcasts that only the system can send; `MY_PACKAGE_REPLACED` goes
  only to the updated app itself.
- The decision moved to the pure `RestoreTrigger.decide(action, wantsConnected, consentGranted)`:
  - any other action → `IGNORE`;
  - the user did not leave the VPN connected (manual disconnect, or never connected) →
    `NOT_WANTED`, nothing starts;
  - VPN consent no longer ours → `CLEAR_INTENT` (forget the intent, start nothing);
  - otherwise → `RESTORE`: the same `RESTORE_ACTION` start as after boot, which runs the
    Always-on restore path (ADR-098/121). A duplicate start (system Always-on was faster) is
    ignored by that path.
- `VpnService.prepare()` is called only when the intent is wanted, as before.
- The receiver logs `restore_boot` / `restore_update` with the decision code to the in-memory
  event log (ADR-144), codes only.

## Assumptions

- `isLockdownEnabled()` read while this app's VPN is established returns the real setting (same
  platform rule as ADR-141 for `isAlwaysOn()`, AOSP `getVpnIfOwner`). Not checked on a device.
- `startForegroundService` from `MY_PACKAGE_REPLACED` is allowed on Android 12+ (it is in the
  platform's list of broadcasts exempt from the background foreground-service start restriction,
  like `BOOT_COMPLETED`). If a device refuses, the receiver catches it and the user reconnects from
  the app, as for boot today.
- After an update the persisted intent (`wantsConnected`) and the vault survive (same app data);
  the restore engine then renews from the saved session as after boot.
- `VpnService.onCreate()` runs once per instance, and `onDestroy()` once per instance, on the main
  thread.

## Security and privacy

- **Protector scope is not widened.** The protector is still only the app's own `VpnService`
  instance. Its uses are unchanged: `protect()` on native renewal sockets to the API origin only
  under Lockdown (ADR-140), `setUnderlyingNetworks` (ADR-146) and the Always-on read (ADR-141).
  It is never passed to Dart, and no other socket, host or app is exempted. What changed is only
  that UI-started tunnels now have one, so the same ADR-140 behaviour applies to them. The
  protected API relay for the restore engine (`ProtectedApiRelay`) is still created only on a
  restore start.
- **Lockdown read.** A wrong `true` would only choose the protected path (the app's sockets
  already bypass the VPN, ADR-132); a wrong `false` would choose a direct path that Lockdown blocks,
  which is the failure being fixed. A pre-establish read can no longer produce that wrong `false`.
- **`MY_PACKAGE_REPLACED` starting the VPN.** It can only start the VPN when the user's own
  persisted intent says "connected" and consent is still ours; otherwise it does nothing (pure
  gate, tested). No third party can send the broadcast. The update may come from the app's own
  updater (ADR-107) or a store; both cases restore only that intent.
- **Option (a)** — see 6.2: no credential becomes readable before first unlock; the cost is an
  unprotected window before unlock for users without Lockdown.
- No new permission. `RECEIVE_BOOT_COMPLETED` was already declared; `MY_PACKAGE_REPLACED` needs
  none.

## Open TODOs carried by this plan (owner-requested, unchanged by this phase)

- **Health probe URL is still `none`** (`DEAD_PEER_PROBE_URL` in
  `apps/client/android/app/src/main/AndroidManifest.xml`; ADR-148 "Health URL placeholder"). The
  probe cannot run until a real project-owned `https` health endpoint exists and is set there.
- **The health probe may never run on Android**: the app excludes its own package from the VPN
  (ADR-132) and Android may refuse to bind the probe socket to the VPN network (`bind_refused`).
  Needs a device check and a routing decision (TODO in `TunnelProbeRunner.probe`, the manifest and
  ADR-148).
- **Restart order** (ADR-146): verify on a device that `establish()` clears the underlying network
  and that "restart, then `setUnderlyingNetworks`" is the right order (TODO in
  `ClientApplication.restartTunnel`).
- **Play Store exact alarms** (ADR-147): check Google Play policy for `SCHEDULE_EXACT_ALARM` and
  the `systemExempted` FGS type before any Play release (TODO in the manifest and
  `NativeRenewer.schedule`).

## Known limitations — NOT verified

- **No device run.** Lockdown behaviour, the protected renewal for UI-started tunnels,
  `setUnderlyingNetworks` on UI-started tunnels, restore after reboot and restore after an update
  were not observed on any phone.
- **No Gradle run, no APK.** Manifest merge, lint and `testDebugUnitTest` were not run. The prepared
  SDK tree in `.cache` (`sdk-go-only-c5-20261113`) does **not** contain this patch change; a build
  against it now fails the pinned-hash check, by design, until a fresh tree is prepared.
- **The new GoBackend callback was compiled only against a scratch copy** of the upstream SDK
  (`git archive` of the pinned revision plus `git apply` of the new patch, outside the repository);
  the shared `.cache` was not modified. The scratch tree differs from `c5` only by the two intended
  hunks.
- No Play policy check (the exact-alarm TODO remains open).
- If an OEM delays or drops `MY_PACKAGE_REPLACED` or refuses the background start, the VPN stays
  off until the user opens the app (same as boot today).
- Pre-unlock traffic without Lockdown is unprotected (option (a), accepted).

## Verification

- Java (deviation from `gradlew testDebugUnitTest`): the same `javac --release 17` + JUnit 4.13.2
  substitute as ADR-140..148, but compiled against the scratch SDK tree with this patch applied
  instead of `.cache/.../sdk-go-only-c5-20261113`. **253 tests passed**: 246 in the main run plus
  7 from `apps/client/android/app` (3 `RestartInPlacePatchTest` + 4 `SystemIntegrationWiringTest`).
  New: `AlwaysOnStatusTest` (+2: Lockdown taken only from an established read, a pre-establish read
  keeps it), `ServiceProtectorTest` (5: UI-started instance becomes the protector, protected
  transport under Lockdown, stale stop keeps the current one), `RestoreTriggerTest` (4: boot and
  update gate, manual disconnect, revoked consent, other actions ignored),
  `SystemIntegrationWiringTest` (4 static guards: no pre-establish Lockdown read and a read in
  `beginWindow` after `tunnelUp`; `serviceCreated` wiring and its place in the patch; pinned hash
  equals the patch hash; manifest has both actions and nothing direct-boot aware).
- Dart: no Dart change. `flutter test` from `apps/client` — **315 passed**. `flutter analyze` on an
  ASCII-path copy (lib, test, pubspec, analysis options): no code issues; the one expected warning
  that `assets/orbit/` was not copied.
- `ClientApplication`/`BootReceiver` wiring has no behavioural JVM test; it is compiled and
  guarded only by the static tests above.

## Owner steps (NOT VERIFIED — after building from this branch)

1. Prepare a fresh SDK tree for the new patch (never overwrite an existing one), for example
   `python scripts/prepare_android_poc.py --target-dir .cache/i-011/sdk-go-only-c6-<date>`, then copy
   the unchanged `jniLibs` and `assets` from `c5` after checking that only `GoBackend.java` differs
   (the ADR-098/127 practice), and build with `-PawgPreparedDir=<that tree>`.
2. Always-on + Lockdown on, connect **from the app**, screen off past a renewal: the event log shows
   native renewal over the protected path and no refusal; `underlying_set` (not
   `underlying_skipped no_service`) after connect and after a network switch.
3. Same with Lockdown off: native renewal uses the direct path (unchanged behaviour).
4. Connected, then reboot: nothing connects before the first unlock; with Lockdown no app has
   network before unlock; after unlock the VPN comes back (`restore_boot restore`).
5. Connected, then install an update of the app: the VPN comes back without opening the app
   (`restore_update restore`). Disconnected, then update: the VPN stays off
   (`restore_update not_wanted`).
