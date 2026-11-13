# ADR-140 — Window renewal without an open app; wait for the network instead of giving up

**Status:** proposed 2026-11-13 (scheduled run "vpn-renewal-without-open-app"). Device verification
**not done**.
**Amends:** ADR-121 (native renewal through protected sockets), ADR-136 (the restore engine may hand
renewal to native), ADR-139 (no attempt budget ends a wanted connection).

## Requirement (owner)
The VPN keeps working for several hours with the screen off and with the app closed or killed (the
phone stays powered on). Every failure ends in a named state with one next action.

## What the code does before this ADR (verified at this branch)
- A window lasts 90 s by default and at most 900 s (`v3_contracts.py`: `DEFAULT_WINDOW_SECONDS`,
  `MAX_WINDOW_SECONDS`; `window_connection.dart` `maxWindowMs`). The beta release scripts build with
  `WindowSeconds = 900` (`scripts/release_all.ps1`); the plain build default is 90.
- Native renewal (`NativeRenewer`) starts only for long windows (`windowSeconds != 90`,
  `window_connection.dart` `_startNativeRenewal`) and only from the UI channel
  (`ClientActivity.java` `startNativeRenewal`). The restore engine's channel answers `false`
  (`ClientApplication.handleRecoveryCall`), and `RenewalOwner.nativeMayStart` refused native renewal
  while a restore engine was alive or Lockdown was seen.
- The app excludes its own package from its VPN (`ClientProfile.packagesForMode` adds `ownPackage`
  in `all`/`exclude` mode, and `include` mode never lists it). So without Lockdown the renewer's
  plain `HttpsURLConnection` already leaves through the physical network; no protection is needed.
- Under Lockdown, Android blocks traffic of apps outside the VPN. The renewer's sockets are not
  protected, so they were assumed blocked; ADR-121 rejected protecting them because
  `HttpsURLConnection` has no hook to `protect()` its raw socket before it connects.
- A `VpnService` instance (needed for `protect()`) reaches app code only on an Always-on start, a
  sticky restart or a restore (`GoBackend.AlwaysOnCallback.alwaysOnTriggered(service)`). A
  UI-started tunnel does not expose it (the field in `GoBackend` is private; ADR-139).
- After a restore (process killed, Always-on, boot) renewal was done by Dart timers in the headless
  restore engine, woken by a 4-minute allow-while-idle alarm that Doze stretches to about 9 min.
- ADR-139 ended recovery after 5 Dart and 8 native attempts in `recovery_exhausted`, which clears the
  user's intent. An outage longer than about 1.5 h therefore stopped the VPN for good.

## Decision
1. **Protected native transport under Lockdown.** `ProtectedHttps` opens a plain `Socket`, calls
   `VpnService.protect()` on it **before** `connect`, then layers the platform TLS socket factory on
   it (`SSLSocketFactory.getDefault()`, system trust and the app's network security config, SNI from
   the host name), verifies the host name with the platform default verifier, and speaks a minimal
   HTTP/1.1 exchange (`Connection: close`, Content-Length or chunked response, 64 KiB cap, 8 KiB
   header cap, header values without CR/LF). TLS and certificate checks are the platform's; only
   HTTP framing is new code, and it is pure and JVM-tested. No third-party HTTP stack is added.
   `RenewalOwner.transport(lockdown, protectorKnown)` picks the path:
   - Lockdown off → `direct` (the existing `HttpsURLConnection`; unchanged behaviour).
   - Lockdown on and a `VpnService` instance known → `protected`.
   - Lockdown on and no instance known → `none`: native renewal is refused, as before.
   The instance is kept from `alwaysOnTriggered` until `alwaysOnStopped` for that instance.
2. **The restore engine hands renewal to native.** The restore channel now accepts
   `startNativeRenewal` with the same checks as the UI channel (tunnel generation, tunnel up, deadline
   set) and the same transport rule. The token is the connection-scoped renew token the Dart engine
   already fetches (`POST …/renew-token`), never the session or refresh token. Once native renews,
   Dart (UI or restore) only observes (`snapshot.nativeRenewal`), as for the UI since ADR-097. The
   single-owner rule (ADR-136) becomes: the UI channel may not start native renewal while a restore
   engine is alive (it must adopt first); the restore engine may. So after a process kill, an
   Always-on start or a boot, renewal no longer depends on Dart timers in Doze.
3. **Lockdown (explicit).** Can protected sockets reach the API under Lockdown? Evidence in the
   code: the tunnel's own transport sockets are protected (`go-only.patch`, `socket_protection_failed`)
   and must reach the node under Lockdown for any Lockdown VPN to work, and the restore relay
   (ADR-121) already relies on protected sockets getting out under Lockdown. Android's VPN code is
   understood to exempt the VPN owner package from the Lockdown block. **None of this is verified on
   a device in this run.** Lockdown therefore stays a documented limitation: no claim of hours under
   Lockdown, and with Lockdown and a UI-started tunnel (no instance known) native renewal is still
   refused, so renewal needs the app open. The existing home-screen hint for `lockdown` already says
   so (`alwaysOnHint('lockdown')`).
4. **Wait for the network and retry; no attempt budget for transient causes.**
   - Native (`ConnectionSupervisor`): `MAX_ATTEMPTS` and the `EXHAUSTED` action are removed. While
     the network is not usable the supervisor waits (`waiting_for_network`, no attempt counted, the
     alarm is cancelled, a network callback re-evaluates). When the network comes back
     (`networkReturned`) the backoff wait is cleared once, so recovery starts right away. Otherwise
     the backoff is 30 s doubling to **5 min** (was 30 min), ±25 % jitter. A failed attempt reserves
     no quota (ADR-099/130).
   - Dart (`RecoveryBudget`): the first five attempts keep the 2 s … 60 s steps; after that each
     attempt waits the last step (60 s) for as long as the user wants the VPN. `take()` never returns
     null; `_exhaustRecovery` is no longer reached for transient codes.
   - Terminal states stay only for terminal causes: lost VPN permission, rejected credentials or
     device, exhausted quota, invalid configuration, user disconnect (`ConnectionSupervisor.terminal`).
   - The reason code `recovery_exhausted` is kept (category, message, diagnostic allowlists) so a
     state reported by an older build is still named. Nothing in this build produces it. The
     diagnostic allowlists are unchanged.
5. **Window length and quota are not changed.** See the follow-up proposal below.

## State machine (summary)
| From | Event | To | Shown reason / next action |
|---|---|---|---|
| connected | window renewed by native/Dart | connected | — |
| connected | window ends or tunnel drops | reconnecting / failed with cause | cause message; automatic |
| reconnecting | no usable network | waiting_for_network | «Проверьте сеть»; automatic when it returns |
| waiting_for_network | network returns | reconnecting (immediate) | — |
| reconnecting | attempt fails, network up | reconnecting after backoff (≤ 5 min native, 60 s Dart) | cause message |
| any | terminal cause | failed, intent ended | existing message |
| any | user disconnect | disconnected | — |

## Security considerations
- No new endpoint, permission or credential. The renew token stays in memory only and is scoped to
  one connection (ADR-097). The restore engine already held a session to fetch it.
- `ProtectedHttps` talks only to the validated API origin (https, host only, port 443), with the
  platform trust store and host-name verification; redirects are not followed; bodies are capped.
- Protecting a socket only exempts it from this app's VPN routing; it is used only for the API
  origin, never for user traffic.
- Endless retries are cheap and bounded in rate: native at most one attempt per backoff (≥ 30 s,
  ≤ 5 min), none while offline; failed attempts reserve no quota.

## What this cannot guarantee (not claimable)
- **Lockdown:** not verified on a device (decision 3). A UI-started tunnel under Lockdown still needs
  the app open for renewal.
- **OEM battery managers** may kill the process or block background starts (ADR-134); the user must
  allow unrestricted battery use.
- **Doze** stretches allow-while-idle alarms (about 9 min in deep Doze). With 90 s windows native
  renewal is not used at all and the window can end in Doze; hours with the app closed need long
  windows (beta builds request 900 s).
- **Force-stop** cancels alarms and stops everything until the app is opened.
- **Reboot:** recovery after boot depends on `BootReceiver` and Always-on; not verified here.
- **User-disabled background activity** or a revoked VPN permission cannot be recovered without the
  user.

## Trade-offs
- Endless retry replaces ADR-139's terminal `recovery_exhausted` for transient causes. The owner's
  hours requirement wins: a dead network for an hour now comes back by itself. The UI still names
  the current cause and its single action while it retries.
- Minimal HTTP framing is new security-relevant code; the alternative, a third-party HTTP client
  with a socket-factory hook, would add a dependency for one call path. TLS stays in the platform.
- Keeping the restore engine alive while native renews costs memory but keeps ADR-121's hand-over.

## Proposed follow-up (not implemented): longer windows
900 s is the server cap. With deep Doze stretching alarms to about 9 min and a renew margin of
`max(55 s, window − 300 s)` = 600 s for 900 s windows, one missed wake still leaves time. A longer
window (for example 1800 s) would tolerate two missed wakes, but each window reserves its byte
budget up front (ADR-130), so a longer window holds more reserved quota per device and returns it
later on a drop. Changing `MAX_WINDOW_SECONDS` needs a server ADR on quota reservation and node
fences; it is out of scope here.

## Verification status
- JVM: `ProtectedHttps` request/response framing and protect-before-connect; `RenewalOwner`
  transport and owner rules; `ConnectionSupervisor` endless retry, 5 min cap, network return.
- Dart: `RecoveryBudget` slow-retry; controller keeps retrying past five attempts.
- **Not verified on a device:** hours of uptime, screen off, app closed or killed, reboot, Lockdown.

## Owner phone steps (NOT VERIFIED — build from this branch, 900 s windows)
Record only pass/fail, the time of any drop, the status text and the diagnostic code.
0. Settings → Apps → DetourVPN → Battery → «Без ограничений».
1. **Screen off 3 h.** Connect, wait for «VPN активен», press power once. Leave 3 h (at least 1 h off
   the charger). Wake: key icon present, a site loads, app shows «VPN активен» without a tap.
2. **App closed 3 h.** Connect, swipe the app away in Recents, screen off 3 h. Wake: key icon present,
   a site loads. Open the app: it adopts the running VPN.
3. **Process killed.** With «Постоянная VPN» on (Lockdown off), connect, then kill the process (e.g.
   `adb shell am kill` while in background, or the OEM task killer, not force-stop). Within a few
   minutes the key icon returns; after 1 h a site still loads (renewal from the restored engine
   through native).
4. **Reboot.** «Постоянная VPN» on, connected; reboot and unlock. Within 2 min the key icon is back;
   after 1 h a site still loads.
5. **Network outage.** Connect, airplane mode for 20 min, then off. Expected: the app shows a network
   cause, not `recovery_exhausted`, and the VPN comes back by itself within about a minute.
6. **Lockdown (limitation check).** Turn on «Блокировать соединения без VPN», repeat step 3, and
   record whether the VPN survives 1 h. Not a pass/fail of this ADR.
7. **Force-stop (expected limit).** Settings → Apps → DetourVPN → «Остановить»: VPN stays off until
   the app is opened.
