# ADR-196 — Windows kill switch (W-01)

**Status:** implemented, **not leak-tested**. The code is unit-tested with a fake firewall backend
and builds; it has never changed a real firewall. The setting is **off by default** in beta.37.
W-01 stays **not met** until the owner's leak test below passes.
**Issue:** I-032, gap W-01. **Related:** ADR-095 (Windows client, admin rights), ADR-124 (user
intent), ADR-194 (in-place restarts briefly remove the TUN), ADR-195 (API bypass hosts),
ADR-132 (Android Always-on/Lockdown).

## Problem
The Windows tunnel is the engine's TUN adapter plus routes (`auto_route`, `strict_route`). When the
engine exits, crashes or is restarted in place (ADR-194), the adapter and its routes disappear and
Windows sends traffic directly. Windows has no OS Always-on for a third-party VPN.

## Decisions (owner-delegated)
1. **Setting** «Блокировать трафик при обрыве», stored in the native prefs file
   (`kill_switch\t1`), **default off**. The owner turns it on (or makes it the default) only after
   the leak test. Turning it on applies at the next connect; turning it off removes an active block
   at once (the user's escape hatch).
2. **Mechanism:** Windows Filtering Platform user-mode API (`fwpuclnt`, Windows SDK), no driver, no
   third-party code. Provider, sublayer (weight 0xFFFF) and filters are added in a session opened
   with `FWPM_SESSION_FLAG_DYNAMIC`, so Windows deletes them when the session handle closes,
   including when the process crashes or is killed; nothing persists across a reboot. Written from
   the API documentation. Own provider key `{3b619d4d-ec79-430a-80ba-9a98d3d0cf6d}`, sublayer
   `{9d242034-a459-40e1-b9dc-c96dde6b0c6c}`.
3. **When engaged:** from a connect with the setting on until the user's wish ends: the Dart
   `endUserIntent` (`setIntent {connected:false}`: user disconnect, sign-out, «Сбросить», any
   failure nobody retries), the setting turned off, or the app closing (controller destructor).
   A plain native `disconnect` does **not** remove it, because recovery stops the engine between
   attempts. The block is installed **before** the engine starts, so start-up is protected too.
4. **Filters** (layers `ALE_AUTH_CONNECT_V4/V6`, `ALE_AUTH_RECV_ACCEPT_V4/V6`):
   block all (weight 0); permit loopback; permit `detour-engine.exe` by WFP app id; permit DHCP
   (UDP 68↔67, DHCPv6 546↔547); permit the TUN adapter by LUID while it exists; permit
   `DetourVpn.exe` outbound TCP to the API IPv4 addresses (primary and ADR-195 fallbacks) only.
   Permits are soft (other firewalls' blocks still apply). IPv6 is otherwise blocked.
5. **Finding the TUN:** the adapter carrying the fixed TUN IPv6 ULA (`kTunIpv6Ula`). The watch loop
   polls it while the block is on and replaces the rule set atomically (one WFP transaction) when
   the adapter appears or changes; the permit is dropped when the engine stops.
6. **DNS during a gap: fail closed.** No DNS is permitted outside the tunnel. While the block is on,
   the controller never sends API names to the system resolver; a reconnect reuses the addresses
   resolved before the block (kept in memory only, never logged).
7. **Failures:** if WFP cannot be opened or any add fails, the transaction is aborted, nothing is
   half-installed, and Connect fails with `kill_switch_failed` (Russian text in `messageFor`);
   the VPN does not start without the block. If the TUN permit cannot be added while up, the
   tunnel stops with reason `kill_switch_failed` (the block stays until the wish ends).
8. **Startup sweep:** when the setting is on, the controller deletes leftover filters, sublayer and
   provider registered under **our** provider key only (`OwnedFilterIds`), before the first
   engage. Objects of any other provider are never touched. With the setting off the backend is
   never created or called.

## Known limitations
- **The app's own API calls during a gap** go through the Dart HTTP client, which resolves the host
  name with the system resolver. With DNS blocked, that works only while the name is in the
  Windows DNS cache (or with an IPv4-literal origin). If the cache entry expired, recovery that
  needs a new lease fails and the block stays until the user disconnects. In-place restarts
  (ADR-194) need no API and are not affected.
- **VLESS panel servers by host name:** if the engine resolves the server through the Windows DNS
  client service, a restart during a gap can fail the same way. AWG endpoints are IP literals.
- **App hang while engaged:** the block stays until the process ends; ending it clears the block.
- **Two app instances** would share the provider key; the second instance's startup sweep could
  remove the first one's rules. The app is not designed to run twice.
- **Interaction with the engine's own `strict_route` WFP rules** is unverified.

## Failure modes
| Case | Expected result |
|---|---|
| Engine crash / in-place restart / gap before a new lease | Blocked until a new engine's TUN is permitted. |
| App crash or killed from Task Manager | Dynamic session gone: **fail-open**, no stuck block. |
| Reboot | Nothing persists; no block at boot (no Always-on on Windows). |
| WFP unavailable (BFE stopped, policy) | `kill_switch_failed`; VPN not started. |
| User disconnect / sign-out / setting off / window closed | Block removed. |
| API host changes address while blocked | Recovery cannot reach the API until the user disconnects. |

## Owner leak test (real Windows PC, setting ON) — required before any default-on decision
Use an external IP check page and a second device or router log if possible. Never paste IPs or
endpoints into reports; record pass/fail only.
1. Settings → turn on «Блокировать трафик при обрыве». Connect. Expected: browsing works, the
   external IP is the VPN's; `netsh wfp show filters` (elevated) lists filters named
   "DetourVPN kill switch".
2. Kill `detour-engine.exe` in Task Manager. Expected: until the watchdog restarts it, pages and a
   ping to a public address **fail**; the external IP check never shows the home IP. After the
   restart, browsing works again via the VPN.
3. Kill it three times within 10 min (beyond the restart budget). Expected: still no direct
   traffic while the app shows reconnecting/failed; the app reconnects on a new lease or shows an
   error; still no home IP.
4. Press Disconnect. Expected: browsing works directly at once; `netsh wfp show filters` shows no
   "DetourVPN kill switch" filter.
5. Connect, then end `DetourVpn.exe` in Task Manager. Expected: browsing works directly at once.
6. Connect, reboot. Expected: browsing works after boot; no our filters listed.
7. Connect, switch Wi-Fi network or sleep/resume. Expected: the tunnel recovers; no direct
   traffic meanwhile (no home IP).
8. While connected: DNS leak page shows only the tunnel's resolver; IPv6 test shows no IPv6.
9. With the setting **off**: repeat step 2 — traffic goes direct during the gap (old behaviour),
   and `netsh wfp show filters` never lists our filters.
10. Turn the setting off while blocked (after step 3 failed state). Expected: browsing works.

**Owner decision before default-on:** whether testers get the block on by default, accepting that a
gap with an expired DNS cache can leave a tester offline until they press Disconnect or turn the
setting off; or keep it opt-in for beta.
