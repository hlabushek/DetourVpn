# Rollout: fault tolerance, many devices per node, ping (ADR-096 to ADR-102)

Updated 2026-11-11. Run everything from the repository folder in a normal Windows PowerShell
window. Every tool is a dry run until you add `--execute`. Claude checked all three nodes
read-only that morning: Helsinki, Germany and Netherlands are on Ubuntu 24.04, idle, with clean
journals, and their agent files match the expected baseline.

## What changed

| Area | ADR | Effect |
|---|---|---|
| Long windows | 096 | Access windows up to 15 minutes instead of 90 s |
| Native renewal | 097 | The Android VPN service renews windows itself, so it survives Doze |
| Service restore | 098 | Sticky service, reconnect after reboot, Always-on in release |
| Network/stall recovery | 099 | Tunnel restarts in place after a network switch or a stall; reconnect without limit |
| Sessions and heartbeat | 100 | A lost refresh no longer logs users out; a short node hiccup no longer drops them |
| Pool nodes | 101 | One node serves up to 250 devices at once; existing nodes converted in place |
| Ping | 102 | Measured ping in the server list and on the home screen, with refresh |

## Step 1 — commit (Claude is not allowed to)

The tools deploy only committed code. Two commits: the other overnight session's own files, then
everything else (ADR-096..102 plus the files both sessions touched).

```powershell
git add apps/admin/static/admin.js apps/admin/static/index.html apps/android/app/src/main/java/org/example/vpnpoc/MainActivity.java apps/client/lib/settings_screen.dart services/api/src/vpn_api/admin.py services/api/src/vpn_api/config.py services/api/src/vpn_api/main.py services/api/tests/test_admin.py services/api/tests/test_admin_ui_contract.py apps/client/android/app/src/main/java/org/example/vpnpoc/DnsChoice.java apps/client/android/app/src/main/java/org/example/vpnpoc/UpdatePolicy.java apps/client/android/app/src/test/java/org/example/vpnpoc/DnsChoiceTest.java apps/client/android/app/src/test/java/org/example/vpnpoc/UpdatePolicyTest.java apps/client/lib/dns_choice.dart apps/client/lib/primary_action.dart apps/client/test/dns_choice_test.dart apps/client/test/primary_action_test.dart docs/adr/103-allow-screenshots-android.md docs/adr/104-orbit-second-tap-target.md docs/adr/105-admin-user-telegram-id.md docs/adr/106-user-tunnel-dns.md docs/adr/107-in-app-update.md docs/adr/108-3xui-remnawave-sources.md docs/overnight scripts/prepare_app_update.py services/api/src/vpn_api/app_update.py services/api/tests/test_app_update.py
git commit -m "feat: overnight session work (ADR-103..108)"
```

```powershell
git add apps docs infra packages scripts services
git commit -m "feat: fault tolerance, pool nodes, ping (ADR-096..102)"
```

```powershell
git status --short
```

Expected: only `?? .claude/` is left.

## Step 2 — update the node agent on all three nodes

For each of `helsinki`, `germany`, `netherlands`:

```powershell
uv run --frozen python -m scripts.update_beta_node_agent --node detourvpn-beta-germany --check-baseline
uv run --frozen python -m scripts.update_beta_node_agent --node detourvpn-beta-germany --execute
```

- Expected from the first command: every file is `match`, and the new files are `absent`.
- Expected from the second: a `steps` list ending in `"step=done"`.
- The agent restarts, and anyone connected reconnects by themselves.

## Step 3 — deploy the API (migrations 0023–0025)

```powershell
uv run --frozen python scripts/deploy_beta_api_0025.py --source-rev HEAD
uv run --frozen python scripts/deploy_beta_api_0025.py --source-rev HEAD --execute
```

Expected: `rollout=success` and `database_revision=0025_pool_peer_slots`.

## Step 4 — switch every node to many devices (pool mode)

One node at a time. The node keeps its name and ID, so users' saved choices stay valid.

```powershell
uv run --frozen python -m scripts.convert_beta_node_pool --node detourvpn-beta-germany
uv run --frozen python -m scripts.convert_beta_node_pool --node detourvpn-beta-germany --execute
```

- The dry run must show `agent_code_pool=1` and `api_supports_pool=1`.
- `--execute` does the following:
  1. disconnects the node's users;
  2. rebuilds it as a pool for 32 devices (`--peers N` changes that);
  3. starts it again;
  4. prints `"converted": true`.
- On any failure it puts the node back as it was and says `convert_failed_rolled_back`.

Then open UDP **51899** inbound at each provider, for ping.

## Step 5 — build the app

```powershell
.\scripts\build_flutter_client.ps1 -WindowTunnels -WindowSeconds 900
```

This APK also contains the other session's changes: screenshots allowed again, DNS choice and the
orbit as a second button.

## Step 6 — checks that prove it works (a build is not proof)

1. Two phones plus the Windows client on the **same** server at once: all browse, and
   disconnecting one doesn't affect the others.
2. Screen off for 60 minutes or more on battery, no app opened: pages load immediately after
   unlocking.
3. Switch Wi-Fi ↔ mobile 5 times, and turn airplane mode on for 1 minute: traffic resumes by itself.
4. Reboot with the VPN on: it comes back. After pressing "Отключить": it stays off.
5. The server list shows "NN мс", and "Обновить список и пинг" measures again.

Save sanitized results (no IPs, keys or accounts) under `docs/evidence/`.

## Rollback

- **Node conversion:** automatic on failure. The pre-conversion files stay in
  `/root/detourvpn-pool-backup-<tag>/` on the node.
- **Agent update:** automatic on failure. A backup stays in
  `/root/detourvpn-agent-backup-<id>/`.
- **API:** automatic on failure. The migrations are additive, and 0025 refuses to downgrade while
  pool connections exist.
- **App:** ship the previous APK.
