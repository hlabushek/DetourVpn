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
- Do not enable Always-on in release until the bootstrap path passes physical
  acceptance. A null-intent service start may enter the same fresh-auth bootstrap;
  it must never resume an old peer. UI may link to Android VPN settings and must
  explain that only the user can enable Always-on/Lockdown.

## Consequences

This avoids maintaining a second lease state machine and preserves server-side
ownership fencing. It requires the protected relay, service callback, and
headless entrypoint to work together. Therefore Always-on recovery is not
implemented by manifest configuration alone and remains unverified until the
local tests and later device acceptance pass.

2026-10-25 implementation update: the pinned GoBackend callback now passes its
`VpnService` instance into `ClientApplication`. The application starts a
headless Flutter engine, opens `ProtectedApiRelay` from the encrypted session's
HTTPS origin, and exposes only session storage, node preference, the opaque
relay endpoint, and the existing native v3 tunnel operations on the method
channel. Dart restores/rotates the refresh token, checks node availability,
reconciles and revokes old server state through `WindowConnectionController`,
then requests a fresh lease. It keeps that controller alive for renewal and
native deadline enforcement. A missing or invalid session, node, permission,
API response, or tunnel admission leads to local disconnect and service stop.

The app's regular `onCreate` no longer stops a system-started service before its
callback can run. A disconnected idle service is retained during bootstrap so
the protected relay remains available while the controller first reconciles.
An already active tunnel still closes through the existing backend lifecycle.
The debug manifest advertises Always-on solely for controlled acceptance; the
release manifest explicitly sets `SUPPORTS_ALWAYS_ON=false`.
GoBackend notifies the application when the service is destroyed; only a matching
system-start service identity may tear down the headless engine/relay and clear
its deadline state.

Local Flutter negatives cover missing session and non-HTTPS origin. Existing
relay tests cover fixed-origin protection and invalid local token; the v3
controller/split-routing suites and Android debug build/unit tests pass. This
does not prove system callback delivery, DNS under Lockdown, Android service
survival, lease recovery, or OEM behavior. Physical acceptance is still required
before enabling this in release or making an Always-on/kill-switch claim.

No API/database/schema or staging change is needed for the client-side design.
The full AmneziaVPN client is a separate GPL-3.0 application and is not copied
into this Flutter client; its platform integration can inform future work after
component-level license review.

## Verification required before enabling

- Add/run focused tests proving process/service restart never reuses an old grant
  and all bootstrap failures keep the tunnel down; current local checks cover
  missing-session and invalid-origin rejection, not every startup failure.
- Native tests verify service-start policy and protected-socket restrictions;
  local relay tests pass, but system-start callback behavior remains unverified.
- Inspect merged manifests and the pinned backend patch: debug advertises
  Always-on for controlled testing; release remains explicitly disabled.
- Physical Android acceptance separately verifies Always-on, Lockdown,
  per-app routing, DNS/IPv6 policy, and recovery after service failure.
