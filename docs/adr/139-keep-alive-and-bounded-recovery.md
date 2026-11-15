# ADR-139 — Keep the VPN alive with the screen off or the app closed; bounded recovery with a terminal end state

**Status:** proposed 2026-11-13 (connection reliability, run 4). Device verification **not done**.
**Amended by:** ADR-140 (the attempt budgets and the terminal `recovery_exhausted` for transient
causes are removed; native renewal may run from the restore engine); ADR-141 (Always-on is read
only while the VPN is established; reads on start/stop said "off" falsely).
**Amends:** ADR-119 (automatic reconnect had no limit), ADR-124 (adds a terminal state for exhausted
recovery), ADR-129 (new failure category `recovery_exhausted`).

## Requirement (owner)
1. The VPN keeps working while the screen is off, and while the app is closed or killed. The phone
   is not powered off.
2. Every failure ends in a deterministic state with a stated reason and one next action. No
   open-ended "reconnecting" state, and no guess-and-retry by the tester (switching servers,
   reopening the app).

## What the code does today (verified at this branch)
- **The tunnel process survives the screen and the app UI.** The VPN runs as the AWG
  `GoBackend$VpnService` foreground service in the app process (ADR-125). Screen-off does not stop
  it. Closing the Activity (Recents swipe) detaches the Flutter engine only (ADR-125 §2). Killing the
  whole process (force-stop, OEM kill) stops everything; only alarms, `BootReceiver` and the user
  can bring it back.
- **Window renewal is the only thing that needs a live owner.** A window lasts 90 s by default and
  up to 15 min when the server allows it (`WindowConnectionController.maxWindowMs`). Renewal has
  exactly one owner at a time (ADR-136):
  - the Dart UI controller, which exists only while the Activity's Flutter engine exists;
  - the native renewer `NativeRenewer` (`ClientApplication.java`, `startNativeRenewal`), which runs in
    the service process without Dart. The UI starts it after a connect. It is **refused under
    Lockdown** (`RenewalOwner.nativeMayStart`, `ClientActivity.java` ~373): its HTTPS sockets are not
    protected, so they cannot leave a Lockdown VPN;
  - the headless restore engine (ADR-121), renewed by Dart timers woken by a 4-minute
    `setAndAllowWhileIdle` alarm (`scheduleRestoreWake`).
  So with the app closed, renewal continues only when native renewal is active. Under Lockdown, or
  when native renewal did not start, the window ends at its deadline and the tunnel stops with
  `lease_expired`.
- **Automatic reconnect was open-ended.** `WindowConnectionController._scheduleAutoReconnect` retried
  `_retryCodes` with backoff capped at 2 min and **no attempt limit** (`window_connection.dart`,
  former `_backoff`, test "auto reconnect has no hard cap"). The UI stayed in `reconnecting`
  indefinitely. The native supervisor (`ConnectionSupervisor.evaluate`) had the same shape: a 30 min
  cap and no end.
- **Always-on** is detected only on an Always-on start (`alwaysOnTriggered`), where `lockdown` is read.
  The app never shows whether Always-on is configured.

## Decision
1. **Keep the mechanisms that exist** for renewal and for the tunnel process: the foreground service,
   native renewal when allowed, the restore engine with alarm wake-ups, the boot receiver. No new
   service, no new network path, no server change (no window-length change, no API change, nothing
   deployed).
2. **Bounded automatic recovery with one terminal failed state** (new reason `recovery_exhausted`):
   - Dart (`lib/recovery_budget.dart`, `RecoveryBudget`): at most **5 automatic attempts** per
     failure streak, delays base × {1, 2.5, 7.5, 15, 30} (2 s … 60 s with the default base). A
     successful window resets the budget. A **user tap** (`connect()` not from auto-reconnect) resets
     it too. When the budget is spent the controller sets `phase = failed`, `error = recovery_exhausted`,
     ends the native intent (`endUserIntent`, so native does not retry behind the user's back) and
     stops scheduling. The Russian text names the cause and the single action: check the network,
     then tap «Подключить».
   - Native (`ConnectionSupervisor`): at most **8 recovery attempts** per streak (attempts reset after
     10 min up, as today). When the budget is spent, the supervisor returns `Action.EXHAUSTED`, which
     `ClientApplication.evaluateRecovery` turns into: intent cleared (`setWantsConnected(false)`),
     recovery alarm cancelled, `lastStop = recovery_exhausted`, category `recovery_exhausted`, which
     is terminal. Native backoff is 30 s doubling to 30 min, so the budget spans roughly 1.5 h of
     background attempts.
   - Both budgets count only failed attempts; neither changes what a window costs (a failed attempt
     reserves no quota, ADR-099/130).
3. **Always-on status** is shown when the platform exposes it. Android exposes it only on a
   running `VpnService` instance (`isAlwaysOn()`, `isLockdownEnabled()`, API 29+). Native reads it
   whenever it gets an instance (`alwaysOnTriggered`: Always-on start, sticky restart, restore;
   `alwaysOnStopped`: service destroyed), persists the last value, and reports the additive snapshot
   key `alwaysOn` = `on` | `off` | `lockdown` | `unknown` (`AlwaysOnStatus`). Before the first read
   (fresh install, UI-only connects without a stop yet) it is `unknown` and nothing is shown. When
   it is `off` the home screen shows a Russian hint with the Android path (Настройки → Сеть и
   интернет → VPN → DetourVPN → шестерёнка → «Постоянная VPN»; Samsung path included) and the
   battery exemption. When it is `lockdown` the hint warns that renewal then needs the app open.
   The hint never changes the state machine. A UI-started tunnel does not expose the GoBackend
   service instance to app code; exposing it would need a change to the tunnel patch, not done.
4. **A reopened UI shows native exhaustion.** If native spent its budget while no UI was open, the
   snapshot says `stopCategory = recovery_exhausted` and `desiredConnected = false`; the UI shows
   the terminal failed state with the same reason and message instead of a silent «VPN выключен».
5. **Diagnostic allowlists** get `recovery_exhausted` in both `diagnostic_report.dart` and
   `DiagnosticReportText.java` (kept identical by the existing test).
6. Existing reason codes are unchanged.

## State machine (summary)
| From | Event | To | Reason / action for the tester |
|---|---|---|---|
| connected | window ends / tunnel dropped with time left | reconnecting (budget −1 each attempt) | none, automatic |
| reconnecting | attempt connects | connected (budget reset) | none |
| reconnecting | budget spent (Dart 5, native 8) | failed, intent ended | `recovery_exhausted`: check network, tap «Подключить» |
| failed | user taps «Подключить» | reconnecting (budget reset) | — |
| any | terminal reason (`vpn_revoked`, `quota_exhausted`, `auth_rejected`, `invalid_config`) | failed, intent ended | existing messages |
| any | user disconnect | disconnected | none |

## Security considerations
- No new endpoint, credential, permission, or network path. The budget only *stops* retries.
- A terminal state clears the native intent, so no background start happens after exhaustion.
- No traffic data, addresses or keys in the new code or the reason.

## Trade-offs (accepted)
- After the budget is spent, the app stops by itself and needs one tap. This is deliberate: a
  perpetual "reconnecting" state is what the owner rejected. The cost is that a long outage (e.g.
  a dead network for an hour) ends in `recovery_exhausted` instead of silently coming back. The
  budget resets on any tap or successful window.
- A bounded native budget trades a background reconnect after an hour-long outage for a clear end
  state. Raising `MAX_ATTEMPTS` is a one-line change if the pilot shows it is too short.

## What this cannot guarantee (not claimable)
- **Renewal under Lockdown with the app closed.** Native renewal is refused under Lockdown and Dart
  is not running, so the window still ends (`lease_expired`). The restore engine can renew, but
  only on alarm wake-ups, which Doze stretches to about 9 minutes (ADR-118/121). This is a real
  gap; closing it needs a protected native HTTPS path (rejected in ADR-121) or an Always-on
  configuration without Lockdown.
- **OEM battery managers** may kill the process or refuse background starts (ADR-134). The user
  must allow unrestricted battery use for the app.
- **Doze** stretches alarms; deep Doze may delay a renewal past the margin.
- **Force-stop** from Android settings cancels alarms and stops everything until the app is opened.
- **User-disabled background activity** or a revoked VPN permission cannot be recovered without the user.
- No claim of end-to-end reachability or DDoS/allowlist compatibility (unchanged).

## Verification status
- Unit tests: `RecoveryBudget` and the controller's exhaustion path (Dart); `ConnectionSupervisor`
  budget and `EXHAUSTED` transition, `RenewalOwner` lockdown/restore rules (JVM).
- Build check: native sources compile against Android SDK 36 (`javac`); no APK was built or installed.
- **Not verified on a device.** The steps below are the only evidence that screen-off, app-killed
  and reboot behaviour works.

## Owner phone steps (NOT VERIFIED — run on a real phone with a build from this branch)
Record for each test only: pass/fail, minutes until a drop (if any), the status text shown, and the
diagnostic code. No addresses, keys or traffic in notes.

0. Preparation: install the build; Настройки → Приложения → DetourVPN → Батарея → «Без
   ограничений». Open the app and note the Always-on hint (shown only if Android reported `off` or
   Lockdown; nothing is shown while the status is still unknown).
1. **Screen off (app open in background).** Connect, wait for «VPN активен». Press the power button
   once (screen off, phone stays on). Leave it 30 min on the charger, then 30 min off the charger.
   Wake the phone: the VPN key icon is in the status bar, a website loads, and the app shows
   «VPN активен» without a tap. Fail: icon gone, or the app shows a failed state (note its code).
2. **App closed.** Connect. Open Recents and swipe DetourVPN away. Screen off for 30 min. Wake:
   the key icon is present and a website loads. Then open the app: it shows the running VPN
   (adopted), not a new connect.
3. **App killed by Android, then recovery.** Connect, then Настройки → Сеть и интернет → VPN →
   DetourVPN → turn «Постоянная VPN» on (Lockdown off). Swipe the app away; screen off for 30 min.
   Expected: VPN still up. Repeat once with «Блокировать соединения без VPN» on and record whether
   it drops; this is the documented Lockdown limit, not a pass/fail of this change.
4. **Reboot.** With «Постоянная VPN» on and the VPN connected, restart the phone and unlock it.
   Within 2 min the key icon appears and a website loads without opening the app.
5. **Terminal state.** Connect, then turn on airplane mode for 5 min with the app open. Expected:
   the status ends in the failed state with «Не удалось восстановить подключение…» (code
   `recovery_exhausted`) within about 2 min, not an endless «Восстанавливается подключение». Turn
   airplane mode off and tap «Подключить» once: it connects.
6. **Force-stop (expected limit).** Настройки → Приложения → DetourVPN → «Остановить». The VPN
   stops and stays off until the app is opened. This is a platform limit, not a failure.
