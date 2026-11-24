# ADR-196 — Windows kill switch (W-01): design, not implemented

**Status:** proposed. **Not implemented in code.** This ADR records the design, the failure modes
and the blocker that stopped the work in this run. Beta.37 ships **without** a Windows kill switch;
ADR-095's "No kill switch" limit still applies.
**Issue:** I-032, gap W-01. **Related:** ADR-095 (Windows client), ADR-194 (in-place restarts
briefly remove the TUN), ADR-195 (API bypass hosts), ADR-132 (Android Always-on/Lockdown).

## Problem
The Windows tunnel is the engine's Wintun adapter plus routes (`auto_route`, `strict_route`).
When the engine exits, crashes, or is restarted in place (ADR-194), the adapter and its routes
disappear. Until a new engine is up, Windows sends traffic directly over the physical network. The
user wanted the VPN and did not get it. Android has system Always-on/Lockdown for this; Windows has
no OS equivalent for a third-party VPN.

## Proposed design
1. **Mechanism: Windows Filtering Platform (WFP) in a dynamic session.** This is the system API
   `fwpuclnt.dll`, so there is no third-party dependency and no driver. The app opens a WFP
   session with `FWPM_SESSION_FLAG_DYNAMIC`. Every object added in it (provider, sublayer,
   filters) is deleted by Windows when the session handle closes, **including when the process
   crashes or is killed**, and nothing survives a reboot. WireGuard for Windows uses the same
   pattern. Our code would be written from the API documentation, not copied.
2. **When it is engaged.** From the moment the user's wish to be connected starts (the Dart
   intent) until the user disconnects, signs out, or exits the app. It covers gaps between
   engines: crash, in-place restart, new lease and the recovery backoff. It is **never** engaged
   when the user has not asked for the VPN, and every user disconnect removes it before the call
   returns.
3. **Filters** (own sublayer, layers `ALE_AUTH_CONNECT_V4/V6` and `ALE_AUTH_RECV_ACCEPT_V4/V6`):
   - block everything (lowest weight);
   - permit loopback, and DHCP/router discovery needed to keep the physical link;
   - permit `detour-engine.exe` by its WFP app id (the engine must reach the VPN server);
   - permit traffic on the Wintun interface by its LUID while an engine is up;
   - permit `DetourVpn.exe` to the API bypass addresses only (primary plus ADR-195 fallbacks,
     TCP 443). This is the same set the engine routes direct, so recovery can reach the API.
   - IPv6: block everything except loopback, matching the engine's IPv6 reject rule.
4. **Removal paths.** Explicit removal on user disconnect, sign-out and window close (`OnDestroy`);
   implicit removal on process exit or crash (dynamic session); nothing persists across reboot.
5. **Failure to engage.** If `FwpmEngineOpen0` or any filter add fails (no admin token, the Base
   Filtering Engine service stopped, policy), the whole transaction is aborted, nothing is left
   half-installed, and the UI must say plainly that protection is off. No silent claim.

## Failure modes considered
| Case | Result with this design |
|---|---|
| Engine crash / in-place restart | Blocked until the new engine is up (the goal). |
| App crash or killed from Task Manager | Filters vanish with the process: **fail-open**, no stuck block. |
| Reboot | Nothing persists; no block at boot (no Always-on on Windows). |
| App hangs while engaged | The block stays until the process is ended; ending it clears the block. |
| Lost admin rights | The app requires admin (manifest); without it the block cannot be added, and the UI says so. |
| User disconnects | Removed before `disconnect` returns; a test must prove no filter remains. |
| API host changes address while blocked | Recovery cannot reach the API until the user disconnects (same limit as ADR-195). |

## Blocker: why it was not implemented in this run
1. **It cannot be verified here.** Testing it means adding WFP filters to the owner's own PC from an
   elevated process and then killing the engine to prove no leak. That changes the machine's
   firewall state. It needs explicit owner authorisation and a real network check. A unit test with
   no WFP calls would prove nothing about blocking. The task rule is not to ship a half-working
   block.
2. **An open design decision on DNS during a gap.** While the block is on, Windows' DNS client
   (a shared `svchost` service, not `DetourVpn.exe`) must resolve the API host for recovery.
   Choices: (a) permit port 53 to the system resolvers for that service, which lets other apps'
   *name lookups* (not their traffic) out during the gap; (b) permit no DNS, and rely on IPv4-literal
   API origins or on addresses cached before the gap; (c) resolve inside the app. This is a privacy
   trade-off for the owner to choose.
3. **Product text and default.** It needs a setting (on/off) with a Russian explanation, and an
   owner decision on whether beta testers get it on by default.

## Test plan for the implementation (owner-run on a real Windows PC)
- Connect; kill `detour-engine.exe`. An external IP check and a ping must fail until the watchdog
  restarts the engine, and must not show the home IP.
- Disconnect; `netsh wfp show filters` (or `Get-NetFirewall*` for diagnostics) shows no filter of
  our provider; browsing works.
- Kill `DetourVpn.exe` while engaged: browsing works at once (dynamic session gone).
- Reboot while engaged: browsing works after boot.
- Sleep/resume and network change while engaged: the tunnel recovers; no direct traffic meanwhile.
