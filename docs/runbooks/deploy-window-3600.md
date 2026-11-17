# Runbook: deploy the 3600 s access window (ADR-147, Phase 3.1)

Status: **prepared, not deployed.** Nothing on any beta host has been changed.

## What changes
- API: `MAX_WINDOW_SECONDS` 900 → 3600 (`services/api/src/vpn_api/tunnels/v3_contracts.py`),
  `window_runtime.py`, OpenAPI maximum (`packages/contracts/openapi.json`).
- Node agents: `MAX_WINDOW_MS` 900000 → 3600000 (`services/node-agent/window_gate.py`) and the
  agent files that copy it (`window_pool.py`, `window_daemon.py`, `window_peer.py`,
  `beta_slot_bootstrap.py`).
- Source commit: `8d46858` (Phase 3). The API and node changes have no database migration.
  The rollout scripts still treat this as an API deploy, so they run the same way.

## Gate 0: local tests (done, 2026-11-17)
`uv run --frozen --python 3.13 pytest` on the six window test files: **69 passed, 48 skipped.**
The skipped tests need `VPN_TEST_DATABASE_URL` (real PostgreSQL). They have **not** run yet.
Two stale assertions were updated (901 s / 900 001 ms now expect rejection at 3601 s / 3 600 001 ms).
Those edits are uncommitted in the working tree.

## Verified current state (read-only, 2026-11-17)
The scripts' defaults (`--baseline-rev ddf1320`) are wrong for this change: that would deploy 112
commits. Verified values:
- **Nodes (all three):** agent files match commit `91a0633` (`--check-baseline`). `window_pool.py`
  and `latency_probe.py` are present.
- **API host:** alembic `0027_panel_server_sources`. The API files match commit `06d6e94`
  (10 of 10 checked; `v3_contracts.py` matches the pre-3600 version). Only
  `v3_contracts.py` changes in `8d46858`.

So the baselines are: nodes `91a0633`, API `06d6e94`, alembic `0027_panel_server_sources`.
Source commit: `8d46858` (last commit that changes the API or node code).

Read-only dry-run results with these values: API preflight `files=1; baselines=1; target=0027`.
Node dry runs: 6 files replaced and 2 added on each node.

## Order
1. **Node agents, one node at a time**, starting with the canary (`detourvpn-beta-helsinki`).
   Run it when few people are connected: the agent restart drops current connections (clients
   reconnect on their own, ADR-099).
   ```bash
   PYTHONPATH=. python -m scripts.update_beta_node_agent --node detourvpn-beta-helsinki --source-rev <COMMIT> --baseline-rev <BASELINE> --execute
   ```
   Then check the node in the admin panel and connect a test phone. Repeat for germany and netherlands.
2. **API**, only after all nodes are updated:
   ```bash
   python scripts/deploy_beta_api_latest.py --source-rev <COMMIT> --baseline-rev <BASELINE> --before-revision <ALEMBIC_NOW>            # preflight
   python scripts/deploy_beta_api_latest.py --source-rev <COMMIT> --baseline-rev <BASELINE> --before-revision <ALEMBIC_NOW> --execute  # deploy
   ```
   The script takes a database dump and restores the old code on failure.
3. **Client build** with a 3600 s request. Not yet: see the next section.

Wrapper (dry run by default, asks YES before each change):
```bash
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_window_3600.ps1 -SourceRev <COMMIT> -BaselineRev <BASELINE> -BeforeRevision <ALEMBIC_NOW>
```
Add `-Execute` only after reviewing the dry-run output.

## Client (step 3): done in code (ADR-150), not yet built or tested on a phone
The three bounds below are now 3600 s, and `scripts/run_overnight_build.ps1` passes
`-WindowSeconds 3600`. Before the change, the client rejected longer windows in three places:
- `apps/client/lib/window_connection.dart`: `defaultWindowSeconds` is **90** (not 900), and
  `maxWindowMs = 900000`.
- `apps/client/android/app/src/main/java/org/example/vpnpoc/WindowDeadline.java`: `MAX_MS = 900000`.
- `apps/client/android/app/src/main/java/org/example/vpnpoc/NativeRenewal.java`: `windowSeconds > 900`.

Also `scripts/build_flutter_client.ps1 -WindowSeconds` is 90 by default. Change the three bounds to
3600 and update the tests that pin 900 (`apps/client/test/window_connection_test.dart`,
Java tests), then build with `-WindowSeconds 3600`. **Do not do this before the nodes and API
are live**: a client that asks for 3600 s against an old API gets refused.

## Verify after each step
- Node: the agent answers, the admin panel shows the node online, a phone connects.
- API: `/api/v1/ready` is OK, and a window request with `window_seconds: 3600` is accepted
  (a request with 3601 is refused).
- Client (later): the phone gets a grant of ~3600 s and renews before it expires.

## Not verified yet
- PostgreSQL window tests (48 skipped locally).
- Anything on a device with a 3600 s window.
- Node-side window behaviour at 3600 s (the nft gate timeout). This is only covered by unit tests.

## Rollback
- API: the script restores the previous code and the database dump automatically on failure.
- Nodes: each run backs up the replaced files and restores them on failure. After success, re-run
  the same script with the previous commit as `--source-rev`.
