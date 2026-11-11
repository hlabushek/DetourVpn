# Run 3 — what you must do by hand

Nothing was committed, pushed or deployed. No server, VPS or panel was contacted. Nothing was
tested on a phone or a Windows PC.

## 1. Review before committing
- `git diff` mixes your uncommitted work with runs 1–3. Run 3 files are listed in the Run 3 section
  of `docs/overnight/OVERNIGHT_REPORT.md`.
- Suggested commits (stage file by file): (a) ADR-110 username, (b) ADR-111 panel import +
  migration 0027, (c) ADR-112 Windows generator, (d) ADR-113 Android text.
- `packages/contracts/openapi.json` was regenerated; run 3 added only `/api/admin/v1/panel-servers`
  and the `PanelServerImport` schema.

## 2. Decide: rebuild the Windows engine with uTLS (needed for any VLESS test)
The pinned Amnezia Box is built without `with_utls`, and Reality needs it (ADR-112 §4, verified in
the source archive). Without it the engine refuses the config at start.
1. Decide whether to add `"with_utls"` to `windows_tags` in
   `third_party/amnezia-box/source-lock.json`. It changes the engine your AWG testers also run.
2. If yes: rebuild with `scripts/build_engine_windows.ps1`, record the new hash, and re-test an
   AWG connection first (regression).
3. Check the generated config offline. The sample below uses only fake values
   (`*.example.test`, a zero UUID, a fake key). Save it as `vless-sample.json` and run:
   ```bash
   detour-engine.exe check -c vless-sample.json
   ```
   ```json
   {"log":{"level":"error"},"dns":{"servers":[{"type":"udp","tag":"profile-dns","server":"1.1.1.1","detour":"proxy"},{"type":"local","tag":"local-dns"}],"rules":[],"final":"profile-dns","strategy":"ipv4_only"},"inbounds":[{"type":"tun","tag":"tun-in","address":["172.19.0.1/30","fdfe:dcba:9876::1/126"],"mtu":1500,"auto_route":true,"strict_route":true,"stack":"gvisor","route_address":["0.0.0.0/0","::/0"]}],"outbounds":[{"type":"direct","tag":"direct"},{"type":"vless","tag":"proxy","server":"panel.example.test","server_port":443,"uuid":"00000000-0000-4000-8000-000000000001","flow":"xtls-rprx-vision","packet_encoding":"xudp","domain_resolver":"local-dns","tls":{"enabled":true,"server_name":"www.example.test","utls":{"enabled":true,"fingerprint":"chrome"},"reality":{"enabled":true,"public_key":"rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrA","short_id":"0a1b"}}}],"route":{"rules":[{"action":"sniff"},{"protocol":"dns","action":"hijack-dns"},{"ip_version":6,"action":"reject"},{"ip_cidr":["198.51.100.7/32"],"outbound":"direct"}],"final":"proxy","find_process":false,"auto_detect_interface":true,"default_domain_resolver":"profile-dns"}}
   ```
   Expected: no error with the `with_utls` build; the uTLS error above with the current build.
   `check` only parses the config; it does not prove a connection.

## 3. Test link handling (only on your own PC, in a secure place)
- Export one client link from your panel (3x-ui: client → share/QR link; Remnawave: the client's
  `vless://` link). Do not paste it into chat, issues, logs or screenshots: it contains the client
  UUID, which works like a password.
- Paste it only into the admin page, «Серверы» → «Добавить сервер из панели», after the
  migrations are deployed (your decision; not done). Expected: «Сервер из панели сохранён…», or a
  Russian error naming the field (for example a wrong `fp` or `type`).
- If your panel's link has a parameter the parser rejects as unknown, tell the next run which key
  name (not the value).

## 4. Open questions for you
- Subscription links (`<SUB_URL>`) are not supported: the Remnawave and 3x-ui subscription formats
  were not verified offline. Please supply a sanitised sample body (with fake UUIDs and hosts) or
  the official doc page if you want subscription import.
- Which panel transports you use. Only `type=tcp` with Reality is accepted now (no ws/grpc/xhttp).
- Deleting or replacing a panel server is not implemented yet.

## 5. Device tests still needed (nothing was tested on a device)
- Admin page: username column, `@username` search, username sort, panel import form, source
  column — in a browser against a test API with the new migrations.
- Telegram username: log in through the Mini App with a test account that has a username, then
  change or remove the username and log in again; the admin list should follow.
- Windows: a real VLESS + Reality connection needs (a) the `with_utls` engine, (b) a delivery path
  from the API to the client (not built), (c) a live panel server. Not possible yet.
- Android: panel servers are not supported; the Russian text exists in
  `apps/client/lib/panel_servers.dart` but is not shown anywhere yet (no delivery path).

## 6. Privacy policy
- Add one line: the service stores the Telegram username from the last Mini App login for support
  search; it is deleted with the account (ADR-110).
