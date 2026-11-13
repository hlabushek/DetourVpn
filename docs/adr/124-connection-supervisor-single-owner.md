# ADR-124 — Connection supervisor: one owner, explicit user intent

**Status:** proposed 2026-11-13 (connection rewrite, overnight run 2).
**Amends:** ADR-097, ADR-098, ADR-117, ADR-121.

## Context (verified in code at `144080f`)
- The engine and TUN already have one owner: `ClientApplication` (application scope) runs every
  engine call on one worker thread through `AwgProviderV1` → `GoBackend`. Flutter Activities and
  the headless restore engine only send channel calls. This part is correct and is kept.
- **User intent was not separate from engine stops.** The persisted flag `resume_connected` was
  cleared by *every* channel `disconnect` (`ClientActivity.java:209-212`), including internal
  cleanups: the UI's `_reconcile()` stops native before revoking stale connections
  (`window_connection.dart:259-262, 286-287`), `_cleanup()` after any failure, a server switch.
  So when a window ended in the background and the user later opened the app, the reconcile
  cleanup erased the intent and the VPN stayed off until the user pressed «Подключить».
- **No one recovered in the background.** After a native stop with time left or a window end
  (`lease_expired`, `native_restart_failed`) and no Flutter engine attached, nothing started a
  new window until the app was opened or the phone rebooted (`BootReceiver`).
- Dart is the only component that can create a window: it holds the access/refresh token and
  the key proof flow. Native holds only a connection-scoped renew token (ADR-097).

## Decision
1. `ConnectionSupervisor` (pure Java, JVM-tested) is the single authoritative record of:
   user intent (`desired`), an intent generation, the last stop reason and its category, the
   recovery backoff, and the derived transport state and evidence label for the snapshot.
2. Intent changes only on: a successful UI connect (`connectWindow`), an explicit user
   disconnect (new channel method `setIntent {connected:false}`), or a terminal failure
   (permission revoked, auth rejected, quota exhausted, invalid profile). The channel
   `disconnect` now only stops the engine; it never clears intent. Persisted as before in
   `resume_connected` (a boolean, no authorization data).
3. **One recovery owner at a time:**
   - a Dart controller that is attached (UI engine, else restore engine) owns reconnects, as
     today (ADR-099/119 backoff);
   - when none is attached, native decides (backoff 30 s → 30 min, ±25 % jitter, reset after
     10 min up, never while offline, never after a terminal stop) and only *starts* the headless
     restore engine (the existing ADR-098/121 path), which reconciles with the API and asks for a
     new window. Native never extends or reuses a window.
   - The UI, on start, reads `desiredConnected` from the snapshot; if the tunnel is not adoptable
     and the user still wants the VPN, it reconnects on its own (status «Восстанавливается
     подключение»).
4. Generation fencing (kept): `ClientApplication.generation` fences every engine operation;
   `intentGeneration` fences recovery starts; the network generation (ADR-127) fences restarts.
5. Snapshot gains additive keys only: `desiredConnected`, `transport`, `failureCategory`,
   `evidence`, `networkValidated`, `recoveryAttempts`, `uiAttached`. Existing keys are unchanged,
   so older Dart code ignores them.

## Kept, and why
| Piece | Why kept |
|---|---|
| `ClientApplication` worker thread as engine owner | Already serializes every engine call; no second owner exists. |
| `WindowDeadline` | Correct monotonic fencing; tested. |
| `NativeRenewer`/`NativeRenewal` (ADR-097/118/120/123) | The only Doze-capable renewal; one owner per window already enforced (Dart skips renewal while `nativeRenewal`). |
| `TunnelWatchdog` (ADR-099/122) | Correct policy; its restart now really happens (ADR-127). |
| Restore engine + hand-over (ADR-098/121) | The only way to get a new window without the UI; now also started by the supervisor. |
| Dart `WindowConnectionController` | Backend contract owner (window create/validate/renew); its rules are tested by 207 tests. |

## Not done
- Moving window creation into native code (would duplicate the API client and token storage).
- A separate VPN service class: the AWG `GoBackend$VpnService` is the foreground service and
  stays (ADR-125).
