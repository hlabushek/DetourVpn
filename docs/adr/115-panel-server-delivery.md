# ADR-115: Deliver panel servers to devices

Status: accepted for beta, **off by default** (run 4, 2026-11-11). Implemented and unit-tested
locally; not deployed; not verified with a live panel, on a Windows PC or on a phone.
Builds on ADR-111 (import), ADR-112 (Windows generator + `with_utls` engine), ADR-113/114
(Android gap).

## Context
Panel servers (3x-ui / Remnawave, VLESS + Reality) were stored and shown in the admin panel only.
Devices must see them in the same server list as our VPS nodes, with a source label, and connect
where the engine can run VLESS. Today that is Windows only.

## Decision
1. **Off by default.** `VPN_DELIVER_PANEL_SERVERS=true` turns delivery on. Settings refuse it
   without window tunnels and the admin encryption key. Reason: an imported link is **one shared
   panel client**. Every device that connects uses the same panel UUID. Our quota does not meter
   that traffic (the panel meters its own client), and the panel cannot tell users apart or
   revoke one of them. The owner turns it on knowingly.
2. **Same list, opt-in rows.** `GET /api/v1/window-tunnels/nodes` gains `source`
   (`vps` | `panel`) and `protocol` (`awg` | `vless_reality`) on every row. Panel rows (id,
   admin label as name, empty region, `available: true`, no probe) are added only with
   `?include=panel`. Older clients never ask, so they never see a row they would try to open as
   a VPS window. No link content is in the list.
3. **Profile only for Windows.** `GET /api/v1/window-tunnels/panel-servers/{id}/profile`
   (permission `tunnels:manage`, device rate limit, `no-store`) decrypts the stored link,
   re-parses it and returns exactly the generator fields. It refuses with
   `panel_platform_unsupported` (409) unless the session is bound to a non-revoked **windows**
   device. Other codes: `panel_servers_unavailable` (503, delivery off), `not_found` (404),
   `panel_server_unreadable` (503: rotated admin key or corrupted row, so re-import),
   `session_device_mismatch` (409).
4. **Client.** The Dart catalog parser accepts only the pairs `vps`+`awg` and
   `panel`+`vless_reality` (missing fields mean an older API, so `vps`+`awg`). Panel rows show the
   Russian label «Сервер из панели (VLESS)». On Android they are listed as unavailable with
   «Серверы из панели (VLESS) пока не поддерживаются на Android. Выберите другой сервер.» and
   cannot be selected. Panel rows are never hidden.
5. **Windows connect.** Selecting a panel row uses a separate branch of the window controller.
   It creates no lease, no quota window and no renewal. It fetches the profile, validates every
   field and its id, and calls the native `connectVless`. The native
   `TunnelController::ConnectVless` builds the config with `CreateVlessEngineConfig` (same split
   routing, DNS choice and control-plane bypass as AWG) and starts the same engine. One session
   lasts at most 12 h (`kMaxVlessMs`); then the engine stops like an expired lease. The refresh
   loop checks only engine liveness. A dead engine fails and auto-reconnects like `native_down`.
   The AWG path is unchanged. Its engine start code was moved into a shared helper
   (`LaunchLocked`) with identical behaviour, and the existing adapter tests cover it.
6. **Admin list.** `delivered_to_devices` now reports the real setting. `android_supported`
   stays `false` until ADR-114 lands.

## Security notes
- The profile response carries a credential. It is never logged. Pydantic fields use
  `repr=False`, the response is `no-store`, the channel wipes the UUID after use, and the native
  side wipes the generated config.
- Anyone with the profile can use the panel client until the owner rotates it in the panel.
  Per-user panel clients (panel API) remain backlog. ADR-111 forbids panel admin access today.

## Verification (local only)
- pytest `test_panel_delivery.py` (3 tests, real PostgreSQL): default off, opt-in rows, no secret
  in the list, Windows profile exact fields, Android refused, unknown id, no auth, rotated key.
- C++ `adapter_tests` VLESS block: refusals (no engine, 0 ms, over 12 h, no admin, bad key),
  start on the fake engine, one tunnel at a time, settings locked while up, disconnect.
- Dart `panel_delivery_test.dart` (5 tests): Android unavailable reason, mismatched pairs refused,
  Windows connect without a lease, engine stop leads to failure, bad profile never started.
- **Device test needed:** a real Windows connection through a live panel; Android list text.
