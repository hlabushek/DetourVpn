# ADR-113: Android does not support panel (VLESS + Reality) servers yet

Status: accepted for the beta (overnight run 3, 2026-11-11). Gap recorded; no VLESS core added.

## Context
Android tunnels use AmneziaWG through `GoBackend` (ADR-084). The app also bundles Amnezia Box
`libbox` for Android (tags `with_awg,with_gvisor`), but nothing on the live path uses it (ADR-108).
Panel servers (ADR-111) need a VLESS + Reality client.

## Options
| Option | Licence | Size | Background impact |
|---|---|---|---|
| A. Use the already bundled Amnezia Box `libbox` with a `vless` outbound | GPL-3.0-or-later (already accepted for the engine) | Small extra (uTLS compiled in); the current build **lacks Reality**: `android_tags` are `with_awg, with_gvisor` and Reality needs `with_utls` (verified in the pinned source, see ADR-112 §4) | A second tunnel engine in `VpnService`; needs its own lifecycle, renewal and watchdog paths (ADR-097..099) |
| B. Stock sing-box `libbox` | GPL-3.0-or-later | +15–25 MB per ABI (estimate, not measured) | Same as A, plus a second Go runtime in the process unless it replaces Amnezia Box |
| C. Xray-core (`libXray`/`AndroidLibXrayLite`) + tun2socks | MPL-2.0 (Xray) + tun2socks licence | +20–30 MB per ABI (estimate) | Two layers (tun2socks + core); more battery and more failure modes |

Recommendation: **A**, as a separate issue after the pilot, because the binary and licence are
already in the app; it needs a `libbox` rebuild with `with_utls` first. It still needs a versioned adapter with a capability check (AGENTS.md) and a
physical-device test.

## Decision for now
- No VLESS core on Android in this run.
- Russian user-facing text (Dart constant `panelServerUnsupportedAndroid` and helper
  `serverUnavailableReason` in the new file `apps/client/lib/panel_servers.dart`; kept out of the
  owner-modified `connection.dart`):
  «Серверы из панели (VLESS) пока не поддерживаются на Android. Выберите другой сервер.»
- Where it shows: panel servers are not delivered to any client yet (ADR-111, ADR-112), so there
  is no server row to show it on today. When delivery is built, an Android client must list the
  panel server as unavailable with this text, never drop it silently. The admin panel already
  marks panel servers «Android: не поддерживается».

## Verification
- Dart unit test that the constant exists and is the text above (no device test needed for a
  string; the row behaviour does not exist yet).
