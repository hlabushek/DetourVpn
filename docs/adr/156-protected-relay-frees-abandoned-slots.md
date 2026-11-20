# ADR-156 — The protected API relay frees the slot of an abandoned connection

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Build/unit-test
verified only; not verified on a device.**
**Builds on:** ADR-121/140 (restore engine API calls through `ProtectedApiRelay` under Lockdown).

## Problem (Stage 2 audit)
`ProtectedApiRelay` carries the restore engine's HTTPS (session restore, node list, connection
create, Dart renewal) as an opaque TCP relay with 8 slots (`MAX_CONNECTIONS`). For each connection
`pipeBothWays` started two copy threads and waited for **both** to end (`completed.await()`
without a bound). The upstream socket has no read timeout (`upstream.setSoTimeout(0)`), and the
comment in `copy` ("closing the paired sockets in relay() unblocks the other copy direction") could
not happen, because `relay()` closes the sockets only after `pipeBothWays` returned.

So when the path to the API black-holes (a Wi-Fi that drops mid-request, a dead NAT mapping), the
Dart client gives up after its 8 s timeout and closes the local socket, but the upstream→local copy
stays blocked until TCP itself aborts (minutes). Each such request holds one of the 8 slots; after
eight, `acceptLoop` closes every new connection at once (`permits.tryAcquire()` fails), so every
restore-engine API call fails even after the network is back. A restore that should start a new
window keeps failing (`bootstrapFailed`, native backoff) for as long as the slots stay held.

## Decision
`pipeBothWays` waits for the first direction to end, then gives the other direction at most
`DRAIN_MS` (15 s) to finish, and returns; `relay()` then closes both sockets, which ends the blocked
copy with an exception. A half-closed request (client `shutdownOutput`) still receives its response
within the bound (the existing test covers it). A package-private constructor takes the bound for
tests.

## Alternatives
- A read timeout on the upstream: rejected, it would also cut a healthy long-lived response or an
  idle keep-alive connection the client still uses.
- More slots: rejected, it only delays the same exhaustion.

## Security and privacy
No change to the origin pinning, the token preface (constant-time compare), `protect()` or what is
relayed; payloads are still never inspected or logged. Closing sooner only reduces held sockets.

## Test plan and results
- Java substitute (javac `--release 17` + JUnit 4.13.2, not Gradle): main run **253 passed**; from
  `apps/client/android/app` **9 passed**. New:
  `ProtectedApiRelayTest.abandonedConnectionsToASilentUpstreamFreeTheirSlots` (eight abandoned
  connections to a silent upstream, then a ninth request is still relayed). The existing half-close
  test (`authenticatedConnectionIsProtectedBeforeItReachesFixedOrigin`) still passes.
- No Dart change.

## Not verified
No device run, no real network black hole. Owner check (optional): under Lockdown, with the app in
the background, drop Wi-Fi during a restore and bring it back; the restore should succeed on the next
attempt instead of failing for minutes.
