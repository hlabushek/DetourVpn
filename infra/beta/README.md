# Isolated closed beta configuration and partial deployment

These files are for the separately purchased closed-beta API and VPN-node VMs.
Staging A, control-plane host B, and production are not targets. Host-specific
values and credentials remain outside the repository. Deployment is partial:
the API/DB are loopback-only; public Caddy serves the ACME challenge and a
closed 404-only HTTPS site; the VPN slot runs closed without a node-agent credential.

The control plane uses an independently named Compose project, database volume
and credentials, paths, systemd units, and loopback ports. It reuses the standard
`vpn_migrator`/`vpn_api` role names only inside its separate PostgreSQL instance.
The PostgreSQL image is
reused from `infra/closed/compose.yaml` by pinned digest; no new software
dependency is introduced. The API listens only on loopback port 28000, and the DB
only on loopback port 25433. Public Caddy ingress uses an approved HTTPS IP
certificate; bootstrap serves HTTP-01 challenge files only. The current
`Caddyfile.ip-closed` redirects HTTP to HTTPS and returns 404 for every HTTPS
path until auth, invite, admin MFA and node-credential gates pass.

The hourly maintenance timer uses a database-only environment file and has no
independent supervisor or alert delivery. `infra/beta-node/` contains units and
code for closed AWG slot cores, a scoped veth/nft network path, and the v3 agent.
The dedicated node now has one closed slot but no node-agent credential/API
binding; provider UDP ingress and firewall rule acceptance are not verified.
The Mini App/API origin and Telegram menu URL are not configured for hosted use.
The source-IP-scoped internal route below is only a local Caddy template; it is
not deployed.
The beta API now includes the locally tested closed-Mini-App invite guard
(ADR-056); its source update was deployed with a rollback copy. Auth, Mini App,
invite enforcement and Caddy API ingress remain disabled/closed.
These files are not a working VPN beta or evidence of a live service. See
`docs/runbooks/beta-environment.md` for remaining acceptance gates.

The eventual HTTPS Caddy template allows only the three authenticated v3 window-agent
POST paths from `DETOURVPN_BETA_NODE_IP`, the current Android/Mini App user API
path allowlist, and MFA admin paths. Unknown paths, other `/api/internal/*`
routes and `/api/v1/ready` return 404; the HTTP bootstrap file never proxies API
traffic. The future API-proxy Caddyfile passed `caddy validate` with Caddy 2.11.4
in Kali WSL using TEST ONLY IP placeholders; it is not installed on the beta host.
The future API-proxy template was additionally validated read-only on the beta
API host with its exact Caddy 2.11.7 binary and actual API/node IPv4 values. It was
streamed to `caddy validate` over stdin; it was not installed, adapted into the
active config or reloaded. The active `Caddyfile.ip-closed` still returns 404.
The path allowlist is documented in ADR-059. Validation proves syntax/certificate-
path loading only, not public route behavior or that exposing the API is safe.
Before enabling the proxy, the API auth,
invite-only gate and admin MFA must be configured and reviewed. The beta node must
be the direct TLS peer; do not put an untrusted proxy before the node matcher. Host
firewall rules must use the same reviewed node IP. The application still verifies
node ID, credential, timestamp and nonce. The future template does not install or
operate the node.

The beta API configuration must set `VPN_BETA_INVITES_ONLY=true` after migration
`0016_beta_invites`; the default remains false in local and existing closed
environments. Enroll each Telegram account through the MFA-protected admin
`grant_beta_invite` action before issuing OTP. Revoke with `revoke_beta_invite`;
that disables linked sessions and queues existing peer/window revocation.
