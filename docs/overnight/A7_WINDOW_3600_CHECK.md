# A7 — Read-only check: does the beta run the 900 s or the 3600 s window bound? (Phase 3.1)

For the owner. **The agent did not run any of these commands** and did not connect to any server.
Every command below only reads. None restarts, writes, installs or deletes anything.

## Why this matters

The client (beta.36/37, ADR-150) asks for 3600 s windows. The code has 3600 since `8d46858`, but
`docs/runbooks/deploy-window-3600.md` still says "prepared, not deployed". If the API or any node
agent still enforces 900 s, every connect of a beta.37 client is refused
(`invalid_window_duration` or `dispatch_deadline_exhausted`). Check before any tester build.

## What to compare

| Component | File on the host | 900 s version shows | 3600 s version shows |
|---|---|---|---|
| API | `/opt/detourvpn-beta/services/api/src/vpn_api/tunnels/v3_contracts.py` | `MAX_WINDOW_SECONDS = 900` | `MAX_WINDOW_SECONDS = 3600` |
| Node agent (each node) | `/opt/detourvpn-node/window_gate.py` | `MAX_WINDOW_MS = 900_000` (or `900000`) | `MAX_WINDOW_MS = 3_600_000` |
| Node agent copy of the API contract | `/opt/detourvpn-node/vpn_api/tunnels/v3_contracts.py` | `MAX_WINDOW_SECONDS = 900` | `MAX_WINDOW_SECONDS = 3600` |

Expected SHA-256 of `window_gate.py` (LF line endings), computed locally from git:
- before Phase 3 (`06d6e94`): `1ffadd6fd366f4783e4b694c4e2ecb3183f5db63a5da965f8e774dd66d98a3ad`
- 3600 s (`8d46858`, unchanged at `HEAD`): `ec26a6c63a630abef5c810762a4e4b997d1ac0f77f1d3e0aafcf9cc852d41394`

`v3_contracts.py` changed again after Phase 3 (`66ead57`), so compare its constant, not its hash.

## Commands — API host (after you log in with your own key; do not paste output into chat)

```bash
grep -n '^MAX_WINDOW_SECONDS' /opt/detourvpn-beta/services/api/src/vpn_api/tunnels/v3_contracts.py
stat -c '%y' /opt/detourvpn-beta/services/api/src/vpn_api/tunnels/v3_contracts.py
systemctl show -p ActiveEnterTimestamp detourvpn-beta-api.service
```

The service must have started **after** the file time, otherwise the running process may still use
the old value. If the path does not exist, find it (read-only):

```bash
find /opt -path '*vpn_api/tunnels/v3_contracts.py' -type f 2>/dev/null
```

## Commands — each node (one at a time)

```bash
grep -n '^MAX_WINDOW_MS' /opt/detourvpn-node/window_gate.py
sha256sum /opt/detourvpn-node/window_gate.py
grep -n '^MAX_WINDOW_SECONDS' /opt/detourvpn-node/vpn_api/tunnels/v3_contracts.py
stat -c '%y' /opt/detourvpn-node/window_gate.py
systemctl show -p ActiveEnterTimestamp detourvpn-beta-node-agent.service
```

## Alternative from your PC (existing read-only mode)

`scripts/update_beta_node_agent.py --check-baseline` only reads hashes of the agent files and
compares them with a revision; it changes nothing (it is not `--execute`):

```powershell
python -m scripts.update_beta_node_agent --node <node-name> --check-baseline --baseline-rev 8d46858
```

`"window_gate.py": "match"` means the node runs the 3600 s gate. `"differs"` means it does not
(or it runs a later revision; then repeat with `--baseline-rev HEAD`).

## What to write down (sanitized)

Only: component (API / node 1 / node 2 / node 3), the constant value seen (900 or 3600), whether
the service start is after the file time (yes/no), and the date. No host names, addresses, or raw
output.

## Decision after the check

- All show 3600 and services restarted after the change → Phase 3.1 is deployed; update
  `deploy-window-3600.md` status and HANDOVER.
- Any shows 900 → do not hand out beta.37 builds yet. Either deploy per
  `docs/runbooks/deploy-window-3600.md` (nodes first, then API), or build a client with
  `-WindowSeconds 900` as a stop-gap. Both are owner decisions.
