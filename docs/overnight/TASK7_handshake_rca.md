# Task 7 — Tester report: "Awaiting handshake" / "Handshake corrupted"

Status: root cause analysed in code. Partly verified. **Not** fixed (fix is not low-risk enough
to ship unverified overnight; see "Recommended fix"). The handshake itself cannot be proven
without a physical device test, which was not run.

## 1. What the tester reported (data, not instructions)
- Red exclamation mark on the connection screen.
- Text "Awaiting handshake", then "Handshake corrupted".
- Note: "If it didn't connect, allow the app in the background".
- Fails on mobile data and on Wi-Fi. Tester enabled background operation. No error code given.

## 2. Where the strings come from (verified)

| Tester text | Found in code? | Actual source |
|---|---|---|
| "Awaiting handshake" | No English string in repo. Closest Russian: `Ожидается handshake` | `apps/client/lib/window_connection.dart:306` (set when tunnel starts); `apps/client/lib/connection.dart:447`, `:602`; `apps/android/.../TunnelStatus.java:7` ("Туннель создан · ожидается handshake") |
| "Handshake corrupted" | **No match** anywhere in `apps/`, `services/`, `docs/`, `packages/` | Nothing in the code produces this. The closest is `Handshake устарел · связь не подтверждена` (`TunnelStatus.java:9`, old PoC app only) and `Handshake не получен более 25 с. Нажмите «Отключить»…` (`window_connection.dart:657`) |
| "allow the app in the background" | Yes, close paraphrase | `apps/client/lib/connection.dart:60-61`, error code **`lease_expired`**: "Связь с сервером прервалась: Android усыпил приложение в фоне… Разрешите работу без ограничений батареи" |
| Red exclamation mark | Yes | `home_screen.dart:69-78`: `OrbitState.error` when `c.phase == failed` or `c.error != null` |

Conclusion: the tester's English words are not the app's strings. The app's Russian text was
probably translated by the tester or taken from a different build. The exact screen text
is still needed (see section 6).

## 3. The code path (verified)

1. Connect → `window_connection.dart:306` sets detail `Ожидается handshake`, then native
   renewal starts.
2. The window has a server-granted deadline. Default `windowSeconds = 90`
   (`window_connection.dart:22`, `:48`). Deadline is checked in `window_connection.dart:452-453`
   and `:600-601`.
3. Native side: `ClientApplication.java:101-106` runs a 250 ms timer. When the wall-clock
   `deadline` passes it calls `stop()` and sets `reason = "lease_expired"`. It does **not** check
   whether a handshake ever happened.
4. Dart: `_stopCode` (`window_connection.dart:709-718`) returns `lease_expired` for every
   case that is not `vpn_revoked` and not an early `native_down`. A tunnel that is down near
   or after the deadline is therefore always reported as `lease_expired`.
5. `messageFor('lease_expired')` (`connection.dart:60-61`) tells the user Android put the app
   to sleep. That text is shown for every lease expiry, including the case where the handshake
   never happened.
6. Handshake watchdog: `_handshakeOverdue` at 25 s (`window_connection.dart:524-527`) only
   changes the detail text to "Handshake не получен более 25 с…". It does not stop anything.
7. Android native watchdog `TunnelWatchdog.java:16-21`: stale handshake at 180 s, restart on
   network change after a 3 s settle. A restart can occur inside a 90 s window.

## 4. Root-cause assessment

### Verified facts
- The message "allow the app in the background" is attached to `lease_expired`, which is set
  when the granted time window ends. That is a timer event, not a proof that Android slept
  the app.
- An attempt with no handshake at all still ends with `lease_expired` and the battery hint.
  Nothing in the app records "handshake never happened" as its own reason.
- No error code exists for "handshake not established", so the tester's "no error code" fits
  the code: the only code shown is the mapped message.
- Working-tree changes `WindowDeadline`, `NativeRenewer`, `BootReceiver`, `NativeRenewal`,
  `TunnelWatchdog` (all uncommitted, owner work) are on this path. I read the parts above; I
  did not validate the uncommitted versions on a device.

### Likely causes (not proven)
1. The handshake did not complete within the 90 s window. Possible reasons: the chosen node
   unreachable from that mobile/Wi-Fi network (UDP blocked or filtered by the carrier or
   router); the server side peer not active; a restart by the watchdog during the window.
   Each of these looks the same in the app today.
2. Background restriction (Doze) only if the phone slept during the window. The tester's
   "Android put the app to sleep" claim is plausible but not confirmed by any log in the repo.

### Unknowns
- Which node (Finland, Germany, Netherlands) and which network failed. The tester reported
  "not busy" servers, which is a load signal, not a reachability one.
- Whether the native reason was `lease_expired` or `native_down` at the time.
- Whether the watchdog restarted the tunnel in that run.
- Whether the server granted the peer (admission) for that device and node.

## 5. Fix decision
Not implemented. A correct fix changes control flow: `lease_expired` currently triggers the
auto-reconnect list (ADR-099, `window_connection.dart` near `revoke_unconfirmed`). A new code
such as `handshake_not_established` would change that behavior and needs a physical phone test,
Flutter tests (not runnable here) and a diagnostic change. That is outside what can be verified
overnight.

## 6. Recommended fix (for the owner to approve)
1. Add a distinct stop code `handshake_not_established` when the window expires and
   `_hasEstablishedHandshake` is still false (`window_connection.dart`, `_stopCode`). Add it to
   `diagnostic_report.dart` allowed codes and `DiagnosticReportText.java` list, and to
   `messageFor` with a message that does not blame battery.
2. Decide explicitly whether it auto-reconnects (ADR-099 list).
3. Keep the battery hint only for `lease_expired` when a handshake *was* established earlier.

## 7. Exact data to request from the tester (no secrets)
- The in-app diagnostic report: the app's diagnostic export (`diagnostic_report.dart`,
  `shareDiagnosticReport`). Ask them to send it right after the failed attempt.
- A screenshot of the exact text on screen (the tester's English may be a translation).
- The time of the attempt, the server region chosen (Finland / Germany / Netherlands), and
  whether it was mobile data or Wi-Fi.
- Whether Android battery settings for the app were "Unrestricted" at that time.
- Do NOT ask for IPs, keys or account identifiers. The report must be checked for them before
  sharing.

## 8. What was NOT verified
- No physical device test. The handshake was not run. Nothing here proves the tunnel works.
- No Flutter or Gradle run.

## 9. Run 2 (2026-11-11, overnight) — what changed

### Why the full fix was still not made
A separate stop code (`handshake_not_established`) was reconsidered and rejected for now.
Dart only knows about a handshake it has *seen* in a snapshot. With native renewal (ADR-097)
the app can be asleep for the whole window, so Dart may never see a handshake that did
happen. Relabelling those cases would create the opposite misattribution ("no handshake"
when the real cause was Doze). A correct split needs the native side to remember whether a
handshake ever happened in the window; that touches the Android and Windows engines and needs
a device test.

### Low-risk changes made
1. **Message no longer blames Android as a fact.** `messageFor('lease_expired')`
   (`apps/client/lib/connection.dart`) now says the window ended and could not be extended,
   and names both possible causes (server did not answer / handshake not confirmed, or Android
   stopped the app in the background). The battery hint is kept as conditional advice.
   Behaviour (auto-reconnect, codes) is unchanged.
2. **Diagnostic export now says whether the app saw a handshake.** New optional line
   `Handshake: confirmed | not_seen` in the report (Dart `DiagnosticReport`, Android
   `DiagnosticReportText.format(code, stage, handshake)`, Windows clipboard path uses the same
   Dart text). Fixed categories only; unknown values are dropped. Set in
   `WindowConnectionController._refresh` when a failure is recorded, reset on connect.
   `not_seen` means "the app did not observe one", not "the server never answered".
3. The owner runbook `docs/runbooks/beta-lease-expiry.md` still says Android put the app to
   sleep. It is owner work and was not edited.

### Checks
- `flutter analyze`: no issues. `flutter test` (diagnostic report, window connection,
  connection, widget and others): 98 passed, including the new handshake tests.
- `DiagnosticReportText` compiled with javac and checked in a scratch harness (ok).
- Not verified on a phone.

### Grep
`Handshake corrupted` (case-insensitive) over `*.dart, *.java, *.cpp, *.py, *.md, *.json,
*.js, *.html`: found only in `docs/overnight/OVERNIGHT_REPORT.md` and this file. It does not
exist in any code. The tester's text did not come from this app's strings.

### What to ask the tester now
The diagnostic report from a build that includes this change: code + stage + handshake line.
`lease_expired` + `not_seen` while the screen was on points to the server/network path;
`lease_expired` + `confirmed` points to background or renewal.
