# ADR-169 — Node daemon failure policy: contain native errors, exit only when the core is gone

**Status:** accepted. Code and unit tests in the working tree (not committed: commits were
blocked by the permission system on 2026-11-23). **Not deployed.**

## Context
`window_daemon.tick` caught only `ContractError` and `RuntimeFault`, and the main loop caught
only `RuntimeFault`. Gate and peer operations raise `GateError` and `PeerFault` (plain
`Exception` subclasses), `subprocess.TimeoutExpired` (5 s timeouts on nft/awg) and `OSError`.
Malformed control responses raised `json.JSONDecodeError` or a pydantic `ValidationError`. Any of
these escaped to `main()`, whose `finally` closes every gate; systemd then restarted the agent,
`startup()` fenced every journaled connection and every user on the node had to reconnect. With
`StartLimitBurst=10` in 300 s a repeating error stopped the node for good. Found by the
2026-11-22 audit (H1).

## Decision
1. **Per-command containment.** Each `recover`, `revoke` and `window` command runs in
   `run_command`. A failed `apply` (contract, runtime, native or OS error) is followed by
   `close_grant`, as before for contract errors. If a command still fails, a fixed event
   `window_command_failed` (operation and exception class name only) is printed and the next
   command runs. The server keeps the command due; the next poll retries it.
2. **Expiry and the active list.** A native error in `expire()` is reported and the poll goes
   on. A native error while reading gate state raises `RuntimeFault("gate_state_unavailable")`
   and skips this poll, because a wrong `active_windows` list would make the server revoke live
   peers.
3. **Parse errors are control faults.** Invalid JSON and a response that is not
   `WindowCommands` raise `RuntimeFault("control_response_invalid")`. Phase checks (`recover`
   while ready, `window` while not ready) run before any command or acknowledgement.
4. **Main loop.** `RuntimeFault` keeps its meaning (`control_unavailable` /
   `control_recovered`). Any other exception is reported (`node_loop_error`) and retried after
   3 s. Only an operator identity failure stops the process: `PeerFault`/`GateError` with code
   `operator_core_identity_invalid`, `operator_artifact_untrusted`,
   `operator_server_key_conflict`, `operator_server_port_conflict` or
   `host_namespace_forbidden`, or `FileNotFoundError`/`ProcessLookupError` (the core process is
   gone), seen in 3 consecutive loops. Then the core or its namespace is gone or replaced and
   only a restart (bootstrap + agent) can recover. SIGTERM still ends the loop cleanly.
5. The systemd unit is unchanged in this step.

## Consequences
- A transient nft/awg failure affects only the one connection whose command failed, not the
  node. That connection may be closed with an `unknown` receipt (see audit item H4).
- A sqlite journal error is retried, not fatal. If the journal is persistently broken the agent
  stays up and reports `node_loop_error` every loop until an operator acts.
- Logs carry only fixed event names and exception class names.

## Tests
- `services/api/tests/test_audit_stage2_daemon.py`: a `GateError` in `install_closed` does not
  escape `tick()`; `main()` keeps running after a non-identity native error; `main()` exits
  with 1 after 3 consecutive core identity failures; malformed responses (bad shape, HTML) are
  `control_response_invalid`.
- Related: `test_window_daemon.py`, `test_window_runtime.py`, `test_window_pool.py`.

## Not verified
- No real node, kernel, nft or AWG failure; no systemd behaviour.
