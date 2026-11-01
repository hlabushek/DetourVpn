# ADR-075: continue bounded cleanup after agent resume failure

Status: implemented locally; staging verification pending.

The I-019 cleanup controller resumes a possibly frozen agent before stopping
the agent and endpoint. A failed `systemctl kill ... SIGCONT` previously
aborted before either stop was attempted. Repeated physical receipts showed
unconfirmed B cleanup with both units still active, but did not retain the
failure stage; this defect is a plausible cause, not a proven explanation.

Cleanup records fixed booleans for state-query/resume errors, each stop command,
and receipt-read failure. It attempts both fixed systemd stops even if the
initial state query or SIGCONT fails. A successful result still requires both
stop commands, forwarding restoration, owned-resource absence and the existing
cleanup receipt. A failed forwarding or receipt read cannot become a successful
cleanup. The fixed expiry deadline and unconditional grant removal are not
extended; operator root recovery remains available when cleanup cannot be
confirmed. No generic remote command or new privilege is introduced. The
test-only runner retains only the failed phase and fixed boolean checks from a
controller response, discarding arbitrary fields and exception text. A remote
action failure is reported as a fixed phase without copying diagnostics.
Agent and endpoint stop commands have 30- and 200-second bounds, respectively,
covering their 20- and 180-second systemd limits. The test runner allows up to
480 seconds for stop/expiry SSH actions so the two sequential stops and checks
can finish. Normal successful stops return immediately; this does not renew a
grant or alter its original deadline.

Regression tests use fake commands to show that state-query or resume failure
does not skip either stop and that failed stop, forwarding or receipt reads do
not report cleanup complete.
The approved bounded staging drill must still verify actual systemd and host
restoration on both nodes.
