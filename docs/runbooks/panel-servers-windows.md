# Panel servers (VLESS + Reality) on Windows — owner runbook

Status: code ready (ADR-111, ADR-112, ADR-115, ADR-116); **nothing deployed, nothing tested on a
PC**. Android lists panel servers as «не поддерживаются» (ADR-113/114).

Read before enabling: a panel link is **one shared panel client**. Every Windows user who
connects uses the same panel UUID. Our quota does not count that traffic. The panel cannot tell
users apart, and you can revoke access only by rotating that client in the panel.

## 1. Build the Windows client with the uTLS engine [LOCAL]
The engine must be built with `with_utls` (now in `source-lock.json`). Build it offline and
check the self-test (expected `exit=0` for the new engine):
```powershell
.\scripts\build_engine_windows.ps1 -Build -GoProxy off
```
Then build the zip with that engine (copies the pinned, verified `wintun.dll` if present):
```powershell
.\scripts\build_windows_client.ps1
```
Record the printed `engine_sha256` and the zip SHA-256.

## 2. Import the panel server [ADMIN PANEL]
Admin page → Panel servers → paste one `vless://` Reality link (from the panel's client "share"
or a subscription body; ADR-116 describes the formats). Give it a label. The link is stored
encrypted.

## 3. Turn delivery on [SERVER, you run it]
Add to the API environment and restart the API. It requires window tunnels and the admin key:
```bash
VPN_DELIVER_PANEL_SERVERS=true
```
Rollback: remove the line (or set `false`) and restart. Panel rows disappear from the list.

## 4. Device test needed [WINDOWS PC]
1. Install the new zip. The server list shows the label and «Сервер из панели (VLESS)».
2. Select it and connect. Check that the browser shows the panel server's exit address.
3. Disconnect. Reconnect after switching Wi-Fi.
4. On an Android phone, the same row shows the Russian "not supported" text and cannot be
   selected.
Save pass/fail notes only. No links, addresses or logs in chat.
