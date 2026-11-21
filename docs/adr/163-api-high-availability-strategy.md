# ADR-163 — API high-availability strategy (client side done, server side planned)

**Status:** accepted as the plan. Client parts implemented and committed locally (ADR-161, ADR-162),
unit-test verified only. **Server and infrastructure parts: documentation only — nothing was built,
deployed or changed on any server.**

## Context
The control plane is one FastAPI instance with one PostgreSQL database behind one public origin.
If that host, its provider or its route is down, clients cannot sign in, connect, renew or read
quota, and node agents cannot report. Tunnels that already run are carried by the data plane and
survive only until their access window ends (at most 3600 s, ADR-150).

Goals, in order: (1) a running VPN must not drop because the API is briefly unreachable; (2) a
client must reach a working API instance when one exists; (3) no weakening of authentication,
TLS, quotas, payments or the kill switch.

## Client design (implemented)
- **Fallback origins (ADR-161).** Build value `VPN_API_FALLBACK_ORIGINS` = up to two https origins
  (placeholders: `<API_FALLBACK_URL_1>`, `<API_FALLBACK_URL_2>`). Order: primary, fallback 1,
  fallback 2. A request moves to the next origin only on a transport failure (no HTTP answer); after
  sending started only if it is idempotent or carries an `idempotency_key`; an HTTP answer (any
  status) is final. Only configured hosts are contacted; TLS verification, redirect refusal and
  response validation are unchanged; no plain-HTTP fallback.
- **Offline grace (ADR-162).** When renewal cannot reach any origin, the held lease keeps working
  until the earlier of one hour after the first failure and the lease's own expiry, with a visible
  degraded status. A tunnel that drops meanwhile restarts on the same still-valid lease (bounded).
  No lease is extended or fabricated; a brand-new connect still needs the API.

## Trade-offs
- Strict order (no sticky origin) costs up to one connect timeout (5 s) per request while the
  primary black-holes; chosen for predictability.
- A 5xx is not failed over: with a shared database the other instances usually fail the same way,
  and repeating risks duplicate side effects.
- Offline grace lets a user keep at most one already-reserved window (≤ 1 h) without a fresh
  server check. It is the same exposure as today's window, not new free service.
- Fallback origins are compiled into the build: changing them needs a new release (or DNS changes
  behind stable names, recommended below).

## Security notes
- Every fallback origin must present a valid certificate for its own name. Using names under the
  project's own domain avoids trusting third-party fronting.
- Session tokens go to every configured origin, so every instance is equally trusted: same code,
  same secrets handling, same admin restrictions (MFA, RBAC). An instance that is not fully trusted
  must not be listed.
- The session record stays bound to the primary origin string; all instances must accept the same
  refresh tokens (shared session tables), otherwise a failover signs users out.
- Unknown webhooks stay fail-closed on every instance; provider signature secrets are shared through
  the secret manager, never through the repository.

## Server and infrastructure work still required (not done)
1. **Stateless API instances on independent providers.** Run at least two API instances with the
   same release in two hosting providers / regions. The API already keeps rate-limit buckets and
   sessions in PostgreSQL (no in-process state for correctness); before scaling, review every
   in-process loop or cache (for example the node service loop in `nodes/service.py`) and make
   singleton jobs run on exactly one instance (a database advisory lock or a leader flag).
2. **Shared database with replication and failover.** One primary PostgreSQL with a synchronous or
   near-synchronous replica in another provider; automated or runbook-driven promotion; connection
   strings through a stable name. Ledger, quota, reward and payment tables require the primary
   (no writes to replicas, no multi-primary). Define RPO/RTO; back up and test restores.
3. **Geo-routing or DNS failover.** Either (a) one stable API name with health-checked DNS failover
   or geo-steering across instances (low TTL, 30–60 s), or (b) distinct names per instance used as
   the client's fallback list. Recommended: both — distinct names in the client list, each name
   itself fronted by health-checked DNS, so a provider change needs no client release.
4. **Health checks.** A cheap unauthenticated `GET` health endpoint per instance that checks the
   database write path (not only the process), used by DNS/load-balancer checks and by monitoring.
   The client's dead-peer probe URL (`DEAD_PEER_PROBE_URL`, ADR-148) can then point at a real
   project-owned endpoint.
5. **Idempotency keys for payment and reward operations.** Already required by AGENTS.md and used
   by the tunnel, window, quota, usage, reward, referral and subscription contracts. With several
   instances the key uniqueness must live in the shared database (unique constraints, not
   in-memory sets), and replays must return the original result. Audit every money/reward endpoint
   for this before running two instances.
6. **Node agents.** Node agents also call the API; give them the same ordered origin list and the
   same rules (transport-only failover, idempotent reports).
7. **Native client paths.** Extend the fallback list to native renewal (`NativeRenewer`, ADR-140)
   and to the Always-on restore engine's protected relay, whose relay admits only the primary host
   today (`protected_api_transport.dart`, native relay). Needs a native change and device testing.
8. **Longer windows.** If windows longer than one hour are ever allowed, native must also cap use of
   a lease at one hour after the first renewal failure (ADR-162 "Native side").

## Owner runbook changes (when the server work is done)
- Add a "second API instance" runbook: provision, deploy the same release, attach to the shared
  database, verify the health endpoint, then add its name to DNS and to the client build value.
- Deploys become rolling: one instance at a time, health check green before the next; database
  migrations must stay backward compatible for one release (expand/contract).
- Incident runbook: if the primary provider fails, confirm DNS failover took effect, check replica
  promotion, and watch for clients on offline grace (they recover on the next successful renewal).
- Build runbook: pass `-ApiFallbackOrigins` to `scripts/build_flutter_client.ps1` only with origins
  that are already live and serve the same API; never list a host that is not under the project's
  control.
- Secrets: every instance reads secrets from the secret manager; rotate them on all instances
  together.

## Not verified
Nothing in this ADR's server section exists yet. The client parts are unit-test verified only; no
real failover, DNS, replication or multi-instance run was performed.
