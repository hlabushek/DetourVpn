# ADR-099 — Network-change and stalled-handshake recovery; unlimited auto-reconnect

**Status:** proposed 2026-11-11 (step C4 of the owner-approved fault-tolerance plan).
**Builds on:** ADR-096, ADR-097, ADR-098.

## Context

The client never reacted to network changes, and nothing acted on a stalled handshake. After a
Wi-Fi/mobile switch, or a NAT mapping silently dropped by the carrier, the tunnel could look "up"
with no traffic until the user opened the app. Separately, the Dart controller reconnected at
most 4 times per 10 minutes, and only after an expired or inactive window. A tunnel the phone
dropped while time was left (`native_down`) was never reconnected.

## Decision

### In-place tunnel restart (SDK patch)
`GoBackend.restartInPlace()` does the following, in order:
1. Calls `establish()` again. On a live VPN this replaces the interface without a gap, so traffic
   does not leak outside the VPN.
2. Starts a new userspace device. Its new UDP socket gives a fresh NAT mapping and an immediate
   handshake. The same connection, keys and window are kept: no API call and no new admission.
3. Closes the old device only after that.

On any failure the tunnel is torn down completely (fail closed), and the app reports
`native_restart_failed` (seen as `native_down`).

The Go library is unchanged. The prepared tree `.cache/i-011/sdk-go-only-c4-20261111` differs from
`sdk-go-only-beta-v3-20261026` only in `GoBackend.java`, so its native libraries are reused after
hash verification (same method as ADR-098). The patch hash is
`8ec52ecfca43665a4a6db859a2adf4eb522ebd9009a5c474c9f063b83289fc26`. The ADR-098 tree `c3` is
superseded and can be deleted.

### Watchdog (`TunnelWatchdog`, pure and unit-tested; wired in `ClientApplication`)
- The default-network callback tracks changes. It ignores the first network and counts a lost
  then regained network as a change. After a 3 s settle, it restarts the tunnel unless a handshake
  already happened on the new network. Restarts are at least 15 s apart.
- A 15 s tick, while the CPU is awake, restarts the tunnel when a validated non-VPN network exists
  and there has been no handshake for 180 s. That is 25 s keepalive plus the 120 s WireGuard
  rekey, so it is a real stall. The backoff between restarts is 2, 4, 8, then 10 minutes, and a
  fresh handshake (60 s or less) resets it.
- Nothing happens without a validated network. During an outage the tunnel stays up and fails
  closed, and waits.
- A watchdog exception never stops the tunnel.

### Dart auto-reconnect
- It also triggers on `native_down`. It never triggers on `vpn_revoked` (ADR-098) or on hard
  refusals (auth, funding, capacity, device revoked).
- The hard cap is replaced by backoff: 2 s, 5 s, 15 s, 30 s, 60 s, then every 2 minutes, with no
  limit. A failed attempt reserves no quota.
- An automatic attempt that fails for a temporary reason schedules the next one. The temporary
  reasons are `network_unavailable`, `api_unavailable`, `rate_limited`, `window_node_unavailable`,
  and `revoke_unconfirmed` while a create with an unknown outcome is being settled.
- A user disconnect cancels everything pending.
- New user-facing code `vpn_revoked` (Russian text), added to both diagnostic allowlists.

## Security and privacy
- No new data leaves the device. A restart sends only the usual WireGuard handshake to the same
  node.
- The watchdog reads only handshake time and network validation state. No traffic content and no
  DNS are inspected or logged.

## Limits / not verified
- Not verified on a device. Whether `establish()` replaces the interface without a gap, and how
  OEMs behave, must be checked: Wi-Fi/mobile switches, airplane mode on and off, and a long
  screen-off with carrier NAT timeouts.
- While the CPU sleeps, the watchdog runs only when the device wakes. Renewal alarms (ADR-097) and
  incoming traffic wake it.
- The always-on headless restore path also gets the watchdog, because it runs in the same
  process.
