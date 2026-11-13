# ADR-129 — One reconnection policy per layer, no competing owners

**Status:** proposed 2026-11-13 (connection rewrite, overnight run 2).

## Layers (each has exactly one owner)
| Layer | Owner | Policy |
|---|---|---|
| Transport (same window, same TUN) | native watchdog (ADR-099/122/127) | after a path change: settle 3 s, restart, verify, ≤ 2 retries; stall: 180 s without handshake, backoff 2 → 10 min; never while offline |
| Window renewal (same connection) | native renewer when active (ADR-097/118), else the attached Dart controller | unchanged; only one is active at a time (`nativeRenewal` flag) |
| New window (new connection) | attached Dart controller (UI first, else restore engine); native only *starts* the restore engine when none is attached (ADR-124/125) | Dart: ADR-099/119 backoff (2 s … 2 min); native trigger: 30 s → 30 min, ±25 % jitter, reset after 10 min up |

## Rules
- Retry only while intent is set and the stop reason is not terminal (`ConnectionSupervisor.terminal`).
- No retries while the physical network is unusable; a network becoming usable re-evaluates at once.
- An explicit disconnect bumps both generations and clears intent: pending Dart timers see a new
  `_version`, native alarms are cancelled, a running restore engine is ended.
- Failure categories (snapshot `failureCategory`): `network_unavailable`, `server_unreachable`,
  `window_expired`, `native_core_failure`, `tun_failure`, `permission_revoked`, `auth_rejected`,
  `quota_exhausted`, `invalid_config`, `user_stop`, `transient`. Captive portal shows as an
  unusable network (`transport = waiting_for_network`). DNS failure of the endpoint cannot occur:
  profiles carry IP-literal endpoints (`Profile.parse` refuses hostnames).
