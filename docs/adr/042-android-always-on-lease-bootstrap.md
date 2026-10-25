# ADR-042 — Android Always-on and leased-session bootstrap

2026-10-25. I-029 needs Android Always-on/Lockdown guidance, but the client
currently owns v3 lease reconciliation in a foreground Flutter controller.

## Decision

- Do not advertise or enable Android Always-on until system-start recovery can
  obtain a fresh, server-authorized lease. A saved tunnel config or prior lease
  is never authority to resume a peer.
- Keep `WindowConnectionController` as the sole owner of lease creation,
  device proof, expiry, and revocation. Do not duplicate this protocol in Kotlin.
- A headless Flutter engine will run that controller after a system start. It
  must use a narrowly scoped control-plane transport that works under Android
  Lockdown while keeping the existing Dart API and lease state machine.
- The transport will replace only Dart `HttpClient` socket creation with a
  loopback relay owned by the active GoBackend `VpnService`. The service will
  protect its outbound socket with `VpnService.protect()` and connect only to
  the HTTPS origin bound to the encrypted local session. The Dart client retains
  TLS certificate and hostname verification end to end. The relay will bind to
  loopback only, authenticate each local stream with a fresh random in-memory
  token, and reject arbitrary destinations; it will not proxy arbitrary URLs.
- The pinned GoBackend `AlwaysOnCallback` will launch the headless entrypoint
  from its system-start path. That entrypoint restores the origin-bound session,
  validates the locally saved node against the authenticated API node list,
  then runs the existing controller's reconciliation and fresh lease admission.
  It will never restore a saved peer config or old grant.
- If session refresh, node selection, API reachability, lease ownership, or
  grant validity is missing or ambiguous, leave the tunnel down. Android
  Lockdown may continue blocking traffic; fail open is not acceptable.
- The selected node may be persisted as a non-secret identifier, but it must
  be checked against the authenticated API node list and current availability
  before any new admission. Clear it on logout; it never authorizes a lease.
- Keep account/session secrets in `SecureVault`; never persist access tokens,
  lease grants, peer configs, or server authorization as restart authority.
- Do not set `SUPPORTS_ALWAYS_ON` or remove the GoBackend null-intent stop until
  the bootstrap path is implemented and reviewed. UI may link to Android VPN
  settings and explain that only the user can enable Always-on/Lockdown.

## Consequences

This avoids maintaining a second lease state machine and preserves server-side
ownership fencing. It requires the protected relay, service callback, and
headless entrypoint to work together. Therefore Always-on recovery is not
implemented by manifest configuration alone and remains unverified until the
local tests and later device acceptance pass.

The Dart origin-restricted socket factory and Android opaque relay are now
implemented as isolated components with local socket tests. The relay is not
yet started by the VPN service or exposed to a headless engine, so the manifest
continues to opt out of Always-on and the transport is not usable end to end.

No API/database/schema or staging change is needed for the client-side design.
The full AmneziaVPN client is a separate GPL-3.0 application and is not copied
into this Flutter client; its platform integration can inform future work after
component-level license review.

## Verification required before enabling

- Focused tests prove process/service restart never reuses an old grant and all
  bootstrap failures keep the tunnel down.
- Native tests verify service-start policy and protected-socket restrictions.
- Inspect the merged manifest and backend patch to prove Always-on is enabled
  only with a functioning fail-closed bootstrap.
- Physical Android acceptance separately verifies Always-on, Lockdown,
  per-app routing, DNS/IPv6 policy, and recovery after service failure.
