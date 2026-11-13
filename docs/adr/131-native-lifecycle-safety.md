# ADR-131 — Native engine lifecycle and resource safety (audit)

**Status:** proposed 2026-11-13 (connection rewrite, overnight run 2).

## Audit results (code at this branch)
| Concern | Finding |
|---|---|
| Concurrent start/stop | All engine calls run on `ClientApplication.worker` (one thread); `GoBackend` methods are `synchronized`. No concurrent JNI start/stop. |
| Blocking JNI on the main thread | None: channel handlers post to the worker; only `prepare()` and lightweight getters run on main. |
| Stale completion after cancel | `connectWindow` re-checks the app generation after `provider.connect` and stops if a disconnect arrived meanwhile. Unchanged. |
| Duplicate engines | `setStateInternal(UP)` returns early while a handle exists; `connectWindow` refuses when `isUp()`. `restartInPlace` closes the old device after the new one is up, or both on failure. One engine at a time. |
| TUN fd ownership | `establish()` fd is detached into Go (`awgTurnOn`); Go owns and closes it on `awgTurnOff`. The Java `ParcelFileDescriptor` is closed by try-with-resources after `detachFd`. |
| Socket leaks | Go owns the UDP sockets; `awgTurnOff` closes them. `restartInPlace` turns off the old handle on success and both on failure. |
| Service destroyed during init | `VpnService.onDestroy` synchronizes on the backend and turns the device off; `setStateInternal` waits ≤ 2 s for the service future and throws otherwise. |
| Thread leaks | GoBackend's status thread is disabled by our patch; the renewer and worker are single long-lived executors. |
| Native crash | Not recoverable in-process; the sticky service restarts and the supervisor restores (ADR-098/125). |
| Window expiry during a native op | The op finishes on the worker; the next `enforceDeadline()` (same thread, before any other op) stops it. |
| Repeated cleanup | `stop()` and `setState(DOWN)` are idempotent (DOWN with no handle returns). |

## Decision
No structural change was needed beyond ADR-127 (restart really happens) and ADR-126 (deadline).
`Thread.stop`/interrupt of native code is not used anywhere.
