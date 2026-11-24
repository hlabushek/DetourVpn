# ADR-185 — Dart-side timeouts on native channel calls (ADR-165 item 1)

**Status:** accepted. Code committed. **Not built into an APK, not run on a
phone or on Windows.**

## Context
`WindowConnectionController` awaited native channel calls (`snapshot`, `connectWindow`,
`advanceWindow`, `disconnect`, and others) without a bound. If the native worker never answers,
`refresh()` keeps `_polling` true for good: no further poll runs, and a connect tap that waits for
the running poll (ADR-154) waits forever. The user sees a spinner that never ends. ADR-165 item 1
recorded this and warned that a plain timeout is unsafe: a `connectWindow` that answers after Dart
gave up must not leave a tunnel running that nobody tracks.

What native already guarantees (read from the code, not changed here):
- Android (`ClientActivity`): every tunnel call runs on one serial worker (`app.worker`). A
  `disconnect` (or `setIntent`) bumps `app.generation` on the platform thread *as soon as it is
  received*, before it is queued. `connectWindow` re-checks the generation after
  `provider.connect` and stops itself if it changed; `advanceWindow` and `startNativeRenewal` refuse
  a changed generation. So a stop sent after a timed-out start cancels that start natively, and the
  queued stop runs after it anyway.
- `WindowDeadline.advance` accepts the same generation again as a no-op.
- Windows (`tunnel_channel.cpp`): calls are handled one at a time on the platform thread, so a stop
  sent after a hung start runs after that start returns.

## Decision
Dart-only. No Java or C++ change.

1. **Bounded calls.** Every native call that `refresh`, `connect`, `disconnect` and `forceReset`
   await goes through `_bounded`: `snapshot`, `advanceWindow`, `disconnect`, `publicKey`, `prove`,
   `adoptRestore`, `startNativeRenewal`, `endUserIntent` use `nativeCallTimeout` (20 s);
   `connectWindow` and `connectVless` use `nativeStartTimeout` (60 s). A timeout raises
   `ClientError('native_timeout')`. `permission()` stays unbounded: it waits for the user's answer
   to the system consent dialog.
2. **Late answers are never applied.** A timed-out call's result is dropped. The controller counts
   outstanding late calls (`_lateNative`) and remembers a late start (`_lateStart`).
3. **Nothing new while native is unanswered.** While a late call is outstanding, `_bounded` sends
   no new non-stop call (it would only queue behind the hung one), `connect()` is refused at once
   with `native_timeout` (no API call, no lease, no quota), and the poll does only the server-side
   revoke retry. Stop calls are always sent: stopping is the fail-closed direction.
4. **A timed-out start is stopped twice.** `_connect`'s failure path stops native at once (the
   normal cleanup; on Android this also cancels the late start through the generation fence) and
   revokes the lease only if that stop answered (ADR-164 rule). When every late call has settled,
   the controller sends one more stop (`_stopLateStart`), so a start that answered late, on any
   platform, is not left running. Only a stop that answered clears `_lateStart`; otherwise the next
   poll retries it.
5. **Reconcile before the next connect.** That final stop marks the controller unsynced, so the
   next poll or connect takes a fresh snapshot and reconciles (and revokes a lease left from the
   failed start) before anything new starts.
6. **Poll timeouts stop nothing.** A monitor `snapshot` or `advanceWindow` timeout in the poll is
   handled like `native_state_unknown` (ADR-145 2.2): error shown, no stop, no revoke, lease kept;
   the next poll after native answers runs normally and clears the error. A late `advanceWindow` is
   harmless: the next poll sends the same generation again, which native treats as a no-op.
7. `native_timeout` keeps the native "stay connected" intent (ending it is another native call,
   and the state is unknown) and is not retried automatically; the user taps «Подключить» again.
   A reconcile snapshot that times out still ends as `native_state_unknown` (its existing path).

New user text (Russian): «VPN на устройстве не ответил вовремя. Пока он не ответит, приложение
ничего не запускает; незавершённый запуск останавливается. …».

## Security and risks
- Fail-closed direction kept: a tunnel that may be running is never revoked before a stop
  answered; a start Dart gave up on is always followed by stops; no new tunnel or lease is created
  while any native call is unanswered.
- A late start on Android can be briefly up between `provider.connect` and its own generation
  check, as before for a disconnect racing a connect. The native deadline and Lockdown remain the
  outer boundary.
- If native never answers at all (a worker stuck for good), the app now shows `native_timeout`
  instead of a spinner, but it cannot stop the tunnel either; the native deadline is the boundary.
- The final stop could, in theory, stop a tunnel that native's own restore engine started while
  the UI's call was hung. Restore runs when no UI holds the tunnel; this ordering is not proven
  impossible. Fail-closed (a stop, never a revoke under a live tunnel).
- Timeouts are generous (20 s, 60 s) so a slow but healthy device is not cut off; they were not
  measured on a device.

## Not covered
- **Diagnostic report code:** `native_timeout` is not yet in the report allowlist. The Dart list
  must match `DiagnosticReportText.java` (a test checks both), and this change leaves Java alone,
  so the report shows the error as `unknown` with the stage of the call that timed out. Adding
  the code to both lists is a small follow-up.
- No native-side cancellation was added; none was needed for the Android and Windows paths read
  above. **Open question:** whether the Windows `connectWindow` handler can block the platform
  thread long enough to freeze the UI. Dart's timer cannot help there, only native can.
- ADR-165 item 3 (wall-clock connected timer) is not addressed: it needs native to report the
  tunnel start as `elapsedRealtime`, so it is not a Dart-only fix.
- The older `ConnectionController` (v2, `connection.dart`) is unchanged.

## Verification
- `flutter analyze` on an ASCII copy of `apps/client`: no issues.
- `flutter test` on that copy: 362 passed, 0 failed (6 new tests in `test/paranoid_audit_test.dart`,
  group "ADR-185", with a test-only `HangingNative`).
- Not run: no APK, no Android or Windows build, no device, no real hung native worker.
