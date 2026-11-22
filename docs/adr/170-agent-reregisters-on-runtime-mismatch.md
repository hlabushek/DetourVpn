# ADR-170 — The node agent reports server rejections and registers again on a runtime mismatch

**Status:** accepted (note). Code and unit tests in the working tree (not committed: commits
were blocked by the permission system on 2026-11-23). **Not deployed.**

## Context
`Control.post` turned every `HTTPError` into `RuntimeFault("control_transport_unavailable")`, so a
server rejection looked like a lost network. The main loop registered the runtime once
(`registered = True`) and never again. When the database no longer knows the running agent's
runtime (a restore, or a migration from a dump taken before the agent's last start), every poll
answers 409 `window_runtime_mismatch` and the node stays unavailable until someone restarts the
agent by hand. Found by the 2026-11-22 audit (M1).

## Decision
1. An HTTP error answer is reported as `rejected_<code>`, where `<code>` is the server's
   `error.code` read from at most 4 KiB of body and accepted only if it matches `[a-z_]{1,64}`.
   Anything else (HTML from a proxy, an empty or oversized body, an unexpected code) is
   `control_rejected`. Transport failures stay `control_transport_unavailable`.
2. On `rejected_window_runtime_mismatch` the main loop prints `control_runtime_mismatch` and
   sets `registered = False`; the next loop registers again. `start_epoch` compares the
   server's current epoch, so this cannot overwrite a newer runtime.
3. Other faults do not trigger registration. The existing `control_unavailable` /
   `control_recovered` events are unchanged, so log tooling keeps working.

## Consequences and limits
- No API or contract change.
- If the restored database still holds windows of an older runtime without receipts, the server
  answers `ready=false` with `recover` commands. An agent that already admitted windows refuses
  them (`recovery_phase_conflict`, now contained per command by ADR-169), and admissions on that
  node stay closed until the agent restarts. Receipts the agent holds for windows the restored
  database never saw make the poll answer 404 `window_receipt_not_found`. Both still need an
  operator; this ADR covers the common case (nothing in flight from the lost epoch).

## Tests
- `services/api/tests/test_audit_stage2_daemon.py`: a 409 with `window_runtime_mismatch` is
  `rejected_window_runtime_mismatch`; HTML, empty, oversized, upper-case and over-long codes are
  `control_rejected`; the main loop registers again after every mismatch and not after other
  faults.
- Related: `test_window_daemon.py`, `test_window_runtime.py`, `test_window_pool.py`.

## Not verified
- No real database restore and no real node.
