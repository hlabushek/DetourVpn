# ADR-127 — Network handover: a restart that really rebinds, and a usable-network test

**Status:** proposed 2026-11-13 (connection rewrite, overnight run 2).
**Amends:** ADR-099, ADR-122.

## Context (verified in code)
1. `GoBackend.restartInPlace()` (our patch, `apps/android/patches/go-only.patch`) calls
   `setStateInternal(tunnel, config, UP)` while `currentTunnelHandle` still holds the live
   handle. `setStateInternal` returns early when a handle is set (prepared tree
   `GoBackend.java:379`, upstream "Tunnel already up"). So every watchdog restart since ADR-099,
   including the ADR-122 network-switch restarts, did nothing: no new interface, no new UDP
   socket, no new NAT mapping. The old socket stays bound wherever the kernel put it. This
   matches the owner's beta.28 report (no traffic after Wi-Fi → mobile until airplane mode).
2. The watchdog and the "connected" status required a network with Android's `VALIDATED` flag
   (`hasValidatedNetwork()`). On networks where the system connectivity check is blocked but the
   VPN endpoint is reachable, the watchdog never acted and the UI never said "connected".
3. Only `onAvailable`/`onLost` were observed. A new local IP on the same network (DHCP renew,
   mobile re-attach) was not treated as a path change.

## Native socket facts (investigated, not assumed)
- `awgTurnOn` creates the userspace device and its UDP sockets; Java then `protect()`s them
  (`awgGetSocketV4/V6`). The sockets are not bound to a `Network`; with
  `setUnderlyingNetworks(null)` and `protect()` they follow the app UID's default network.
- The prebuilt Go library exports no "rebind"/"bump sockets" call (only TurnOn/TurnOff/GetSocket/
  GetConfig/Version), and rebuilding it is out of scope tonight. Migrating a connected socket is
  therefore not possible; the correct recovery is to **recreate the transport**.

## Decision
1. `restartInPlace` clears `currentTunnelHandle` before `setStateInternal(UP)`, so a new
   `establish()` + `awgTurnOn` really happens: new device, new protected sockets on the current
   default network. The old device is closed only after the new one is up; any failure tears
   everything down (unchanged fail-closed path). The TUN is replaced by `establish()` on the same
   `VpnService`, so the VPN stays up for other apps (no full teardown, no direct-routing gap).
2. A new prepared SDK tree `sdk-go-only-c5-20261113` was made with
   `scripts/prepare_android_poc.py` from the pinned upstream revision and the edited patch. Only
   `GoBackend.java` differs from `c4` (checked against the receipts' source hashes); the Go sources
   are identical, so `c4`'s `libwg-go.so` files were copied unchanged. The pinned hash in
   `apps/android/tunnel/build.gradle.kts` is updated to the new reviewed patch
   (`bdbaa8ed…4a85`). No guard was weakened.
3. `PhysicalNetwork` (pure, unit-tested) tracks the default physical network: usable = INTERNET
   and no captive portal; `VALIDATED` is reported separately (`networkValidated` in the snapshot).
   Changes that move the path (other network, return after loss, new local address on the same
   network) bump a generation and arm the existing ADR-122 settle/verify/retry rules. Late
   callbacks for a previous network are ignored. Offline = not usable: no restarts.
4. Debounce stays as ADR-122 (3 s settle, 15 s gap, 2 verify retries); no new delays.

## Not covered / limits
- Not verified on a device. The restart path is the single most important device test.
- IPv6-only / NAT64: the profile endpoint is an IPv4 literal; wireguard-go cannot reach it over
  an IPv6-only network without CLAT. Android's CLAT normally provides IPv4 on such mobile
  networks; not verified.
- `setUnderlyingNetworks(null)` (follow default) is kept; no explicit `Network.bindSocket` since
  the Go sockets are not exposed for binding before they are used.
