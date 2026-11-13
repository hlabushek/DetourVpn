# ADR-134 — Refused background starts keep the intent; restart reconciles from the persisted intent

**Status:** accepted 2026-11-13 (connection reliability run 3, items C and D; amends ADR-125).

## Context
ADR-125 starts the restore service from native recovery. On Android 12+ a background service start
can be refused. Before this change the refusal only set `recoveryNote`. The supervisor still counted
the attempt, so the backoff grew, and no stop category told the user why nothing runs. After a process
kill the persisted intent was restored, but its rules were neither written down nor tested.

## Decision
### C. Refused background starts
- A refused start is not a failed recovery. `ConnectionSupervisor.startRefused` undoes the attempt
  count, keeps the user's intent, and records the stop category `background_start_refused`. The
  category is not terminal.
- The next retry waits at most one minute (`REFUSED_RETRY_MS`), not the growing backoff. Retries come
  from the next allowed trigger: a network becoming usable, the app opening (a Dart owner takes over),
  a boot, or the alarm. A start that is accepted clears the category.
- The snapshot carries `stopCategory`. Dart maps it to one Russian text. The text promises only what
  the app does: the connection is kept, and recovery runs on the next open, network change or reboot.

### D. Process killed and restore reconciliation
- The persisted intent is the only input after a process restart (`restore`). A restored intent is
  never treated as a fresh user action.
- Rules, all tested in `processRestartReconcilesPersistedIntentOnTheNextTrigger`:
  - intent on, no tunnel, usable network, no Dart owner: recovery starts on the next trigger;
  - intent on, tunnel still running (service survived): the tunnel is adopted, nothing restarts;
  - intent on, no usable network: wait, no retry storm;
  - intent on, Dart owner attached: native does not compete;
  - intent off: nothing starts.
- **Not done:** a tunnel left running with intent off (possible only if the process died between two
  writes) is not stopped automatically. Stopping it needs a new supervisor action and a path that could
  race a UI connect; the risk is higher than the gap. The next user action resolves it. Listed as a limit.
- No permanent foreground notification and no permanent wakelock are added (run 3 constraint).

## Security considerations
- Refusal and restart change no quota, window or credential. Each restart still asks the API for a new
  window (ADR-130).
- The stop category carries a fixed code only, never an endpoint or key.

## Limits / not verified
- Whether the owner's Android version or OEM refuses the start is not known. Device test D.
- `recoveryNote` and `stopCategory` are both kept: the note is diagnostic, the category is the one the
  user is shown.
