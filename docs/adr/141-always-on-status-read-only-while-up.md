# ADR-141 — Read the Always-on setting only while the VPN is established

**Status:** proposed 2026-11-15 (connection reliability). Device verification **not done**.
**Amends:** ADR-139 decision 3 (when native reads `isAlwaysOn()`).

## Problem (owner report, 2026-11-15, beta.33)
On the owner's Pixel 8 Pro, Always-on was set for the app (Lockdown off), yet the home screen
showed «Постоянная VPN выключена» after an overnight run in which the tunnel stopped.

## Cause (verified in code)
`ClientApplication.observeAlwaysOn` read `VpnService.isAlwaysOn()` / `isLockdownEnabled()` in
`alwaysOnTriggered` (GoBackend `onStartCommand`, before `establish()`) and in `alwaysOnStopped`
(GoBackend `onDestroy`, after the tunnel was torn down), then persisted the value.
Android answers both calls only for the caller's *running* VPN: in AOSP,
`VpnManagerService.isCallerCurrentAlwaysOnVpnApp()` returns `getVpnIfOwner() != null && getAlwaysOn()`,
and `getVpnIfOwner` returns null when `Vpn.getUnderlyingNetworkInfo()` is null, which it is while
the VPN is not running. (Reading of AOSP source, not verified on this device.) So every read at
those two moments said `off`, and every tunnel stop wrote a false `off` that the UI then showed.
The hint is a symptom of the stop, not its cause, and it misdirects the tester to settings that
are already correct.

## Decision
1. Native reads Always-on only from `snapshot()` while the tunnel is up (`provider.isUp()` and a
   window deadline) and a service instance is known (`protector`). No read in `alwaysOnTriggered`
   or `alwaysOnStopped`. `AlwaysOnStatus.reading(api, established, …)` returns null ("no reading")
   when the VPN is not established; the previous value is kept.
2. The stored value moves to the key `always_on_v2`; the old `always_on` key (false `off` from
   beta.33 and earlier) is deleted on start. After the update the status is `unknown` (no hint)
   until a valid read.
3. A UI-started tunnel still exposes no service instance (ADR-139), so the status stays `unknown`
   there unless an Always-on start, sticky restart or restore happened in this process.

## Not changed (known, out of scope)
- `startAlwaysOnRecovery` sets `lockdown = service.isLockdownEnabled()` on the Always-on start,
  i.e. before `establish()`, so by the same platform rule it is likely always `false`. Under
  Lockdown native renewal would then choose the `direct` transport (ADR-140). The owner runs
  without Lockdown; fixing it changes renewal transport and needs its own device check.
- Why the tunnel stopped overnight and was not brought back: see the analysis note for this
  issue; it needs the phone's diagnostic report.

## Security
Display only. No new permission, endpoint, data or recovery decision.

## Verification
- JVM: `AlwaysOnStatusTest` (4 tests) pass; all app Java sources compile with `javac --release 17`
  against Android SDK 36 and the patched tunnel classes (plugin registrant stubbed). No APK built.
- **Device (not done):** with Always-on set, connect, then read the home screen: no
  «Постоянная VPN выключена». After a tunnel stop the hint must not appear.
