# ADR-100 — Lost refresh responses and node heartbeat gaps no longer end sessions

**Status:** proposed 2026-11-11 (step C5 of the owner-approved fault-tolerance plan).
**Amends:** ADR-012 (refresh reuse always revoked the family; a lost response required a new login).
**Builds on:** ADR-096 to ADR-099.

## Context

- **Lost responses.** The client erased its refresh token before sending the refresh request.
  Any lost response, timeout, or app kill during refresh forced a new Telegram login. Starting the
  app offline also erased the saved session.
- **Heartbeat gaps.** `available_runtime` treated a node as unavailable once its last heartbeat
  was more than 10 s old. This also applied to existing connections:
  - `GET /window-tunnels/{id}` then omitted `client_config`, and the app treated the window as
    inactive and tore the tunnel down;
  - renewal was refused.

  So a 10-second node-agent restart or network blip disconnected every user on that node.

## Decision

### Bounded refresh replay (server)
- `session_credentials` gets two nullable columns (migration `0024`, with a downgrade):
  - `successor_id`: the credential this one was rotated into;
  - `first_used_at`: the first use of its access token. It is written in its own short
    transaction, so a rollback of the request cannot erase it.
- A replay of an already rotated refresh token is accepted only if all of these hold:
  - it arrives within **120 s** of the rotation;
  - the successor exists;
  - the successor was never used: its refresh has not been rotated and its access token has not
    been used.

  When accepted, the successor is deleted and a new pair is issued.
- Every other reuse revokes the family exactly as in ADR-012. Rotations made before the migration
  have no successor link, so strict behaviour applies to them.

### Client
- The old refresh token stays durable until the new pair has been validated and stored.
- Transport failures (`network_unavailable`, `api_unavailable`, `rate_limited`) keep the session.
  Only an explicit refusal (401) or a storage failure erases it.
- At startup, temporary failures are retried for about 30 s. After that the saved session is kept
  and retried on the next resume or launch. The user is not signed out.

### Heartbeat tolerance
- A new admission (create, and the catalog's `available` flag) still needs a heartbeat 10 s old
  or less.
- An existing connection keeps its config and may renew with a heartbeat up to **120 s** old. The
  renewal stays `pending` until the node acknowledges it. The node's kernel deadline still bounds
  data-plane access.

## Security trade-off
- A stolen refresh token replayed within 120 s, before the legitimate holder has used its new
  tokens, wins silently. The legitimate client then gets `invalid_refresh`. Before this change
  the whole family would have been revoked.
- Concurrent retries now both succeed, and only the last issued pair stays valid. ADR-012 revoked
  the family in that case.
- Theft detection is kept wherever it is observable: as soon as the successor is used (the normal
  client uses it immediately after a refresh), any replay revokes the family.
- Refresh tokens are still stored only as SHA-256 digests. The grace window and its conditions
  are enforced under the existing user, family and credential row locks.

## Found while testing (fixed with the owner's go-ahead)
- `POST /api/v1/auth/link/redeem` (`devices.py`) could redeem one link code twice under
  concurrency. The unlocked `probe` read put the row in the session identity map, and the later
  `SELECT … FOR UPDATE` returned that cached object without refreshing `consumed_at`. At HEAD the
  test passed only through timing.
- Fixed with `.execution_options(populate_existing=True)` on the locked re-read, as other locked
  reads in this codebase already do. `test_concurrent_redeem_of_one_code_succeeds_exactly_once`
  passes on repeated runs.

## Limits / not verified
- Not verified against the deployed API or on a device.
- The 120 s limits are chosen, not measured.
