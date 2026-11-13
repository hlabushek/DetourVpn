# ADR-136 — One renewal owner per window; no native renewal for the restore engine

**Status:** accepted 2026-11-13 (connection reliability run 3, item B; ADR-121 follow-up).

## Context
ADR-121 leaves the restore engine renewing from Dart timers, woken by a 4-minute alarm. The run-2 report
asked for native renewal for that engine. ADR-121 rejected the only way to do it: the native renewer uses
`HttpsURLConnection`, which cannot be routed through the relay or protected, and the restore engine would
have to hand its session token to native code.

## Decision
- **No native renewal for the restore engine is added**, and no flag-gated path is added either. It would
  need the session token in native memory plus a new HTTP path: the risk ADR-121 refused. Device test B
  decides whether the 4-minute Dart wake-up is enough in deep Doze.
- The single-owner rule is made explicit and tested (`RenewalOwner`):
  - native renewal may start only when no restore engine is alive and Lockdown is off
    (`ClientActivity`, `startNativeRenewal`, now refuses while a restore engine is alive);
  - `owners(...)` counts the native renewer, the attached UI and the restore engine; more than one is a
    conflict (`conflict`).
- Renewal decisions are unchanged: `NativeRenewal.due`, `restoreWakeDelayMs` and `burstDelayMs` keep
  their existing tests (`NativeRenewalTest`).

## Security considerations
- The guard only refuses. It never widens what native can do.
- Hand-over (ADR-121) is unchanged.

## Limits / not verified
- The restore engine's Doze behaviour is not proven (device test B).
- When the channel refuses, the UI keeps its own Dart renewal, which is then the single owner.
