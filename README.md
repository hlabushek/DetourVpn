# Project VPN — Telegram sign-in, devices and quota accounting

I-019 (2026-10-22): local selection of one of two operator-configured nodes and
explicit termination of an expired lease when a node is lost. When final counters are unknown,
the full reserve is debited; the client explains this before recovery. Real failover
between two VPS is not yet accepted: a separate approved staging drill is required.
[Setup and drill](docs/runbooks/node-failover.md),
[checks](docs/verification-i-019.md), [ADR-031](docs/adr/031-node-failover.md).

I-017 (2026-10-21): a local Telegram Mini App in the Soft Orbit style is implemented:
balance, bonuses/clicker, devices and history. The server verifies initData, issues a
limited session and uses the shared ledger. Disabled by default;
a real Telegram/HTTPS launch has not yet been verified. [Setup](docs/runbooks/telegram-mini-app.md),
[verification results](docs/verification-i-017.md), [decisions](docs/adr/029-telegram-mini-app.md).

I-012 **technically accepted 2026-10-20 within the bounded staging scope (ADR-024)** — key confirmation,
quota reservation, native peer add/remove, config after ACK and kernel budget/expiry.
On the VPS and a physical Pixel, API revoke, an actual agent restart and expiry on
loss of the control-plane transport passed; cutoff, settlement, network restore
and cleanup were confirmed. [Actual receipt](docs/evidence/i-012-staging-lifecycle.json).
Without a trusted staging profile, admission stays closed.
[Reproduction](docs/runbooks/tunnel-lifecycle-staging.md),
[checks and boundaries](docs/verification-i-012-admission.md).

A local HTTP API works: `GET /api/v1/health` → `{"status":"ok"}`.
This is a process liveness check that works without DB/Redis. Local APIs have been implemented for
Telegram-code authorization, sessions, a device registry and a quota ledger on PostgreSQL.
Production VPN, fleet accounting and payments are not yet implemented. The Flutter MVP is described
in I-013/I-014; I-012 measures native final counters of a bounded staging lease.
I-011 contains an Android native POC with a real handshake and egress
on a physical Pixel 8 Pro through staging; the IPv6 probe, network change,
disconnect and cleanup/deadline were verified. Individual peer revoke with traffic
stop was verified; technical I-011 is accepted within the boundaries of [ADR-022](docs/adr/022-i011-acceptance-boundary.md).
[Results](docs/evidence/i-011-revoke-staging.json).
Build and limitations: [Android POC](docs/runbooks/android-poc.md).

I-007 adds quota balance/history in whole bytes, a one-time starting credit
and internal credit/debit operations with durable replay protection. The starting amount is not yet
chosen: VPN_INITIAL_QUOTA_BYTES defaults to 0. There are no public self-credit
methods. [Quota and reconciliation](docs/runbooks/quota.md), [I-007 checks](docs/verification-i-007.md).

I-008 adds internal processing of cumulative rx/tx: node/peer bindings to the owner,
explicit epochs/reset, replay/late reports, pending on insufficient quota, atomic ledger
debit and reconciliation. [Instructions](docs/runbooks/usage.md), [I-008 checks](docs/verification-i-008.md).
There is no public counter intake; node identity/transport remain I-009. The local CLI
works with trusted data in its own test DB and is not a real agent.

Sign-in path: start an attempt in the client → open the bot via the link → receive a code
in the private chat → enter it in the client → work with your own devices → sign out/revoke
access. The Telegram webhook adapter and sendMessage are implemented, but the **real bot
is not connected**: the token and the approved HTTPS ingress are configured manually, locally.
The scenario was verified by automated tests with a real DB and a fake sender only in tests.
Email/OIDC are not part of this flow. [Manual verification](docs/runbooks/authentication.md).

## Running on Linux Mint

Commands are run from the repository root. `uv 0.11.17`, pinned in
pyproject.toml, is required. If it is missing: `pipx install uv==0.11.17`; pipx itself is installed
separately by your system's means. The system Python is not replaced.

```bash
uv python install 3.13
uv run --frozen uvicorn vpn_api.main:create_app --factory --app-dir services/api/src --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
```

The second command creates `.venv`, installs dependencies from `uv.lock` and starts the API.
In another terminal: `curl --fail http://127.0.0.1:8000/api/v1/health`. Stop: Ctrl+C.
On Windows with Python 3.13/uv the startup command is the same; check in PowerShell:
`Invoke-RestMethod http://127.0.0.1:8000/api/v1/health`.
The Linux commands are documented; the actual verification of this change was done on Windows.

`.env` is optional; see `.env.example` for an example. The HTTP access log is disabled so as not to
store IPs and arbitrary request URLs. Swagger/OpenAPI over HTTP are disabled;
for local development you can set `VPN_ENABLE_API_DOCS=true` in `.env`.
The schema is stored in `packages/contracts/openapi.json`. There is no production configuration.

## Checks

```bash
uv lock --check
uv sync --frozen
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen pytest
uv run --frozen python scripts/export_openapi.py --check
```

Without VPN_TEST_DATABASE_URL, PostgreSQL integration tests are explicitly skipped.
Full run with a temporary real server and the migration commands:
`docs/runbooks/database.md`. Auth/devices summary: `docs/verification-identity-stage.md`.

After a contract change: `uv run --frozen python scripts/export_openapi.py`.
Git hooks: `uv run --frozen pre-commit install`.
CI runs these checks, the Compose check and a dependency audit. The CI setup
is prepared; a remote result will appear only after publishing to a private GitHub.
Local results: `docs/verification-i-001.md`.

## Structure

| Path | Purpose now |
|---|---|
| `services/api/src/vpn_api` | Health/errors, Telegram pairing, sessions, RBAC gate, devices, PostgreSQL |
| `services/api/migrations`, `alembic.ini` | Accounts/authentication/devices/quota, guarded downgrade |
| `services/api/tests` | API, ownership, negative/concurrency, real PostgreSQL; Telegram HTTP mocked explicitly |
| `packages/contracts` | Generated OpenAPI |
| `apps/android` | I-011 native POC: physical handshake/egress/revoke accepted; public release closed by release gates |
| `apps/telegram-mini-app` | I-017 React/TypeScript cabinet; launch via same-origin API, not published |
| `apps/admin` | README, no executable UI |
| `services/worker`, `services/node-agent` | README, no executable agent |
| `infra/compose` | Optional local PostgreSQL/Redis |
| `infra/ansible` | README, deployment not implemented |
| `docs/adr` | Product/native ADRs, local persistence, auth/session/retention and device ownership |
| `scripts/local_auth_client.py` | Manual local client; without saving/printing codes and tokens |
| `scripts/auth_cleanup.py` | Explicit cleanup of expired auth records |
| `services/api/src/vpn_api/quota` | Immutable ledger/receipts, read API and reconciliation |
| `scripts/quota_reconcile.py` | Read-only quota reconciliation without printing identifiers |
| `services/api/src/vpn_api/usage`, `scripts/usage_local.py` | Trusted usage accounting and local ingest/audit |

Review: `docs/architecture-review.md`. The I-002 research is done:
`docs/verification-i-002.md`, `docs/licensing-checklist.md`, `docs/compatibility-matrix.md`.
The ADRs remain Proposed; native build/release gates are open, the SDK is not integrated.
Current status and remaining conditions: `docs/issues/I-002.md`. Infrastructure and environment matrix:
`docs/runbooks/local-development.md`. Conventions: `docs/contributing.md`.
Dependencies and licences: `docs/dependencies.md`.

I-004: [DB instructions](docs/runbooks/database.md), [report](docs/verification-i-004.md).
I-005/I-006 are implemented locally: [launch](docs/runbooks/authentication.md),
[auth contract](docs/adr/012-authentication-implementation.md),
[devices](docs/adr/013-device-ownership.md), [report](docs/verification-identity-stage.md).
Admin MFA enrollment is absent, administrative operations are closed. The local shared
limiter uses PostgreSQL; Redis is not yet connected. Before real users,
privacy/account deletion/recovery and infrastructure gates are needed. The I-007 quota ledger
is implemented locally, the I-008 usage engine is also ready for local review;
the next issue I-009 is the node registry and the authenticated agent contract.

AGENTS and CODEX_START_PROMPT are preserved. TECHNICAL_SPEC v0.2 and ROADMAP were updated
2026-10-18 at the owner's request for sign-in by a code from a Telegram bot; the reason is recorded in the spec.
The original README was moved to `docs/source/startup-kit-readme.md` so that the root
README describes the actual startup. The brand and licence of our code are not chosen.
The repository remains local.
