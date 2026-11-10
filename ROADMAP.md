# VPN service development roadmap with Codex

**Date:** 2026-10-15  
**Authorization clarification:** 2026-10-18, by direct owner decision — sign-in code from the Telegram bot,
see TECHNICAL_SPEC v0.2 and ADR-001. This changes the sign-in method; it is not a completed integration.  
**Baseline mode:** one developer + Codex, working in sprints.  
**Estimate:** a guideline of 12–20 two-week sprints to a confident commercial product; this is not a deadline promised by Codex. The actual pace depends on mastering the Android VPN API, testing, and external legal constraints.

## Stage transition rules
- Each stage has a demonstration (**demo**), measurable acceptance criteria (**DoD**), and an explicit **go / no-go** decision.
- Do not start sales until the security, legal, payment, and reliability gates are closed.
- Do not treat product mockups as a working tunnel, or a local accounting system as finished billing.
- Do not touch production VPS automatically: all production operations require manual confirmation.

## 0. Foundation: repository preparation (sprint 0)
**Goal:** form a managed project, not a chaotic folder of code.

**Tasks:**
- Create a private repository, add `AGENTS.md`, `TECHNICAL_SPEC.md`, `ROADMAP.md`, `docs/adr/`, `docs/runbooks/`.
- Define conventions for naming, API versions, PRs, issue labels, tests, and migrations.
- Create `.gitignore`, `.env.example`, pre-commit, and a basic GitHub Actions CI.
- Build a dev/staging/prod matrix without issuing production credentials.
- Prioritize ADR-001/002/003 before developing the real tunnel.

**DoD:** CI runs, the project starts locally with one documented command; there are no secrets in the repository.

## 1. Backend skeleton and identity (sprints 1–2)
**Deliverable:** API + PostgreSQL + Redis, test authorization.

**Tasks:**
- FastAPI app factory, Pydantic configuration, SQLAlchemy models, Alembic migrations.
- Tables users, auth_identities, devices; sign-in by a one-time code from the Telegram bot per ADR-001.
- A minimal auth bot adapter is already required in I-005: trusted Updates, a pairing code, rate limits, and replay protection. The Telegram Mini App remains a separate I-017; it is not required for sign-in.
- Session model, refresh token revocation, rate limits, user-scoped access.
- RBAC and separate administrator access with MFA.
- OpenAPI, request IDs, minimal healthchecks, pytest tests.

**DoD:** a user cannot read/modify another user's data; repeated login/token revocation are tested; migrations apply from an empty DB.

## 2. Quota ledger — the heart of the economy (sprints 3–4)
**Result:** balance and debits without the clicker and without VPN — fully tested business logic.

**Tasks:**
- `quota_ledger`, credit/debit, idempotency key, unique indexes, transactions.
- Demo usage-samples model with node/peer/epoch/sequence, replay protection.
- Counter reset resolution, ordering of delayed samples.
- API `/quota`, `/quota/history`, units in bytes without floating point.
- Property/concurrency testing: several parallel credits/debits.

**DoD:** a repeated request and races do not change the balance twice; there is a manual procedure for reconciling the balance and the ledger.

## 3. Verifiable VPN sandbox (sprints 5–6)
**Result:** a real tunnel on Android through a test node; a separate simple test harness utility is acceptable.

**Tasks:**
- Run a licence and compatibility spike: base engine version, SDK/bindings, IPv6/DNS, Android requirements.
- One test VPS, restricted SSH rights, a reproducible Ansible playbook.
- Node Agent: issuing/revoking a single peer, read-only traffic counters, status/health.
- Backend provisioning orchestration, storing public keys/peer states.
- Android-native proof-of-concept on `VpnService`, a real connect/disconnect.
- Tests for DNS/IP leaks, network switching, VPN node reboot.

**DoD:** on a physical phone a real VPN is established with a confirmed new egress IP; the configuration is revoked; client secrets are not in the log. Without this the UI is not declared ready.

## 4. Mobile app MVP (sprints 7–8)
**Result:** Flutter UI on top of a really working native VPN integration.

**Tasks:**
- Design tokens, light/dark, onboarding, login, home/connect, regions, quota, errors.
- Flutter ↔ Kotlin bridge with a state machine.
- Handling of network change, foreground/background, `VpnService` denial, logout/revoke.
- Client-side caching without plaintext secrets; encrypted storage for sensitive data.
- Android integration tests and basic interface accessibility.

**DoD:** the full scenario user→registration→VPN→disconnect→logout; the native status is synchronized with the UI.

## 5. Gamification MVP (sprints 9–10)
**Result:** real balances and rewards, without fictitious local increase of megabytes.

**Tasks:**
- Tables reward_rules/claims, server-driven configuration.
- `POST /rewards/claim`: check eligible / daily cap / cooldown / idempotency.
- Daily bonus, clicker with a maximum number of credits; anti-bot restrictions.
- Credit history, automated replay test and parallel-request test.
- Economic config: reward bytes are set after real measurements of costs.

**DoD:** ten parallel repeats of one claim yield one reward; limits are not exceeded; data match on the phone and in the DB.

## 6. Limited alpha test and support (sprints 11–12)
**Result:** a closed group of users and verifiable observations about quality.

**Tasks:**
- Private distribution of a test APK, crash reporting without excessive data collection.
- User instructions, an accessible support channel, a mechanism for sending a diagnostic report with consent.
- Load tests of the API and VPS, measurement of usage-collection delays, memory and battery consumption.
- Monitoring, backup+restore drill, runbooks, emergency node rotation.
- Adjusting free quotas to the real cost.

**DoD:** baseline SLOs are measured, incidents are reproducible, recovery is verified, no critical vulnerabilities.

## 7. Telegram Mini App and referrals (sprints 13–14)
**Result:** a second interface for rewards, a single backend.

**Tasks:**
- React/TypeScript Mini App, server-side validation of Telegram initData.
- Tasks with honest verification; `getChatMember` only where the bot's rights allow.
- Referral codes, qualification event, campaign budget, simple analytics.
- Anti-fraud: reuse of links/tasks, limits, voluntary appeals.
- Legal decision on the admissibility of incentivized subscriptions and advertising.

**DoD:** the Telegram payload cannot be forged; repeated rewards are not credited; partner statistics do not disclose excess personal data.

## 8. Multi-node, failover, second engine (sprints 15–17)
**Result:** readiness for the failure of one node.

**Tasks:**
- A second VPS at an independent provider, engine version compatibility.
- Node registry, health checks, capacity thresholds, signed available-node metadata where needed.
- Automatic suggestion of an alternative node after an error; diagnostic reasons.
- Abstraction of a second transport after a successful acceptance spike.
- Anti-DDoS: assessment of the provider's L3/L4 mitigation; WAF/rate-limit for the API only.
- A procedure for moving users without losing accrued quota.

I-031: for the closed beta, first consolidate the already available
VPN data nodes into an operator inventory and enable them one at a time. The current client/API already supports
manual node selection through a root-owned window manifest; heartbeat/slot capacity
determine the honest catalog status. This does not enable new host/provider changes,
automatic failover, or public availability.

**DoD:** physically powering off node A does not corrupt the balance; node B continues serving new connections; the user is clearly informed.

## 9. Legal/payment/commercial gate (sprints 18–19; only after confirmation)
**Result:** a lawful path to a commercial test.

**Tasks:**
- Legal assessment of the business/customer geography, the owner's age, VPN advertising, the offer agreement, the data processing policy.
- Choose one payment provider with lawful KYC/refunds/reporting.
- PaymentAdapter, signing/replay validation, refunds/chargebacks, subscription lifecycle.
- Tests: duplicate webhook, rollback, subscription cancellation, renewal failure.
- Check App Store/Google Play/Telegram rules for the specific distribution channels.

**DoD:** a positive legal gate decision, a working sandbox integration, all critical payment tests green, refund support prepared.

## 10. Controlled beta → public release (sprint 20+)
**Result:** controlled operation with budget limits.

**Tasks:**
- Security review by third parties, dependency/SBOM audit, key rotation, disaster recovery drill.
- Budget limits for VPS providers and sponsored campaigns.
- Closed paid beta, then audience expansion after stable metrics.
- Marketing tests only in permitted formats in the target jurisdictions.
- Monitoring of churn, traffic cost, chargebacks, penalty and legal risks.

**DoD:** no blocking security/legal problems; there is a real margin on the pilot cohort; users can delete their account and get support.

## 11. Separate R&D track: transport quality and restricted networks

This is a **research track** that does not block the main release:
- Build a matrix of network restrictions: protocol recognition/DPI, IP filtering, strict allowlist, loss of DNS/IPv6.
- Document for each engine: version, OS, network type, operator, outcome, failure causes, stability assessment.
- Test only on networks/infrastructure for which there is authority.
- Do not enable an unverified experimental transport in the global default setting.
- If a strict allowlist excludes the node addresses, **do not claim a working universal bypass**. Obfuscation does not fix the unreachability of the final IP.

## 12. First GitHub Issues (execution order)

| ID | Priority | Issue | Dependency | Acceptance criterion |
|---|---|---|---|---|
| I-001 | P0 | Init monorepo + AGENTS/CI | — | Local run/CI |
| I-002 | P0 | ADR for authorization and VPN SDK | I-001 | ADR accepted, licencing checklist |
| I-003 | P0 | FastAPI skeleton + health | I-001 | `/health` + tests |
| I-004 | P0 | Postgres + Alembic | I-003 | Migrations without errors |
| I-005 | P0 | Authentication + RBAC | I-004 | IDOR and auth tests |
| I-006 | P0 | Devices public keys | I-005 | Device ownership tests |
| I-007 | P0 | Quota ledger | I-004 | Replay/concurrency tests |
| I-008 | P0 | Usage sample ingestion | I-007 | Restart/replay tests |
| I-009 | P0 | Node registry + agent contract | I-004 | Idempotent provisioning tests |
| I-010 | P0 | One test node deploy | I-009 | Repeatable provisioning |
| I-011 | P0 | Android native tunnel POC | I-002/I-010 | Real egress IP |
| I-012 | P0 | Tunnel API + revoke | I-006/I-009/I-011 | Revocation tests |
| I-013 | P0 | Flutter connect UI | I-011/I-012 | End-to-end Android |
| I-014 | P0 | Quota UI/history | I-007/I-013 | Consistent amounts |
| I-015 | P0 | Clicker + daily reward | I-007/I-013 | No double credit |
| I-016 | P0 | Alerts, encrypted backups | I-010 | Restore drill |
| I-017 | P1 | Telegram Mini App | I-015 | Verified initData |
| I-018 | P1 | Referral qualification | I-017 | No self-referral reward |
| I-019 | P1 | Second node/failover | I-012/I-016 | Node-failure drill |
| I-020 | P1 | Payment provider sandbox | Legal gate | Signed webhooks + refunds |
| I-021 | P1 | Security review | I-019/I-020 | High/critical resolved |
| I-022 | P2 | Windows/Linux clients | Stable mobile core | E2E smoke tests |
| I-032 | P1 | Windows beta client (full tunnel, app exclusion, domain routing); see `docs/issues/I-032-windows-beta.md`. Supersedes the Windows part of the I-022 row above, whose ID clashes with the `docs/issues/I-022.md` file (manual subscriptions) | Android beta core, ADR-079 | Owner-run physical Connect on Windows with server-side peer status |
| I-023 | P2 | iOS feasibility | Organization/licence gate | Apple-compliant native POC |

### Local status 2026-10-18

- I-003 skeleton/health was completed as part of the starting I-001; re-implementation is not needed.
- I-004 is implemented and verified on an isolated PostgreSQL: connection layer,
  account schema, Alembic, and tests. Commands/results — docs/verification-i-004.md.
- The CI workflow is prepared; this status does not confirm a remote GitHub run.
- I-005 is implemented locally: Telegram webhook adapter, pairing/code verification,
  opaque access/refresh, revoke/logout-all, shared PostgreSQL rate limits, and an RBAC gate.
- I-006 is implemented locally: public keys of Android devices, owner-only read/list/revoke,
  session binding, concurrent device limit, and a DB ownership FK. Common verifiable
  path: auth → own device → refresh → revoke/logout. Report: docs/verification-identity-stage.md.
- This is a functional milestone within phase 1, **not the full closure of phase 1**:
  admin identity/MFA enrollment is still missing (admin operations are closed), Redis
  is not connected (the local limiter is PostgreSQL, ADR-012), live Telegram is not verified.
  Before real users, account deletion/recovery/privacy/backup gates are needed.
- I-007 is implemented locally: immutable quota ledger/receipts, exact integer bytes,
  credit/debit and durable accepted/rejected replay, owner-only quota/history,
  once-only initial grant, and reconciliation. The starting size is not chosen: default 0;
  setting VPN_INITIAL_QUOTA_BYTES. Results: docs/verification-i-007.md.
- I-008 is implemented locally: internal usage bindings/epochs/receipts, node/peer/
  epoch/sequence, replay/reset/late reports, rx+tx delta, received/charged watermarks,
  pending and atomic quota debit. Historical receipts preserve the epoch boundary;
  a separate read-only reconciliation. Report: docs/verification-i-008.md.
- The local accounting tasks of phase 2 are available for review/demonstration on synthetic
  reports. A real collector/delivery and bounded traffic enforcement are not proven.
- I-009 is implemented locally: node registry with a one-time credential, versioned
  authenticated usage endpoint v1, timestamp/nonce replay protection, and an atomic
  transaction of credential nonce + I-008 receipt/debit. Verification: 224 tests passed on
  a disposable PostgreSQL; `docs/verification-i-009.md`. There is no real daemon/VPS/TLS
  ingress and no credential rotation.
- **I-010 completed 2026-10-19 within the bounded staging test-node acceptance.**
  Pinned binaries/licenses were installed on the approved VPS; the installation and a repeat
  were verified. After fixing the noexec `/run`, the final run gave `ok=48
  changed=2 unreachable=0 failed=0`. Two real namespace exchanges confirmed
  handshake, bidirectional packets, and peer removal; two injected-failure runs —
  cleanup. Existing containers, firewall/routes/links/namespaces were preserved,
  transient units were removed. `docs/evidence/i-010-staging-acceptance.json` was verified.
  Checklist — `docs/issues/I-010.md`, boundary — ADR-018. The release remains
  installed, the test runtime was removed; public endpoint/Android/egress were not verified.
  Linux binary scan: 0 symbol/package findings; release/native gates remain.
- I-011: on 2026-10-19 on a physical Pixel 8 Pro, native UP, a fresh
  handshake, and HTTPS IPv4 egress through the agreed staging endpoint were confirmed. The private key
  stays in the phone's memory. Verified: Wi-Fi → cellular, return to Wi-Fi,
  screen on/off, normal disconnect, and traffic cutoff when the
  node is stopped. The IPv6 probe is blocked inside the VPN and passes after disconnect;
  a sample of three DNS queries showed only the expected Cloudflare resolvers.
  These are bounded measurements, not proof of the absence of all leaks.
  Fixes to sandbox/container routes and snapshot v2 were verified on staging:
  forced-stop and a real 600s deadline restored all seven baseline checks.
  The test node was stopped, resources cleaned up, the temporary sudo grant revoked;
  the instrumentation APK was removed, the POC stopped. 174 Python tests passed,
  166 database tests skipped without VPN_TEST_DATABASE_URL; Android build/lint and
  7 unit tests passed. **Technical I-011 accepted (ADR-022):** the fixed direct
  UAPI revoke really removes the peer while the engine is running and stops the Pixel's
  traffic, the repeat succeeded. After loss of the node, disconnect restored IPv4/IPv6.
  Forced deep Doze IDLE/resume passed; natural hardware suspend and active
  system permission revoke were not measured. Release licence/SBOM gates remain.
  Evidence: `docs/verification-i-011.md`,
  `docs/evidence/i-011-revoke-staging.json`; ADR-019/020/021/022.
- I-012: **technically accepted 2026-10-20 in the bounded staging scope of ADR-024**: migration 0007/0008, owner-only
  requests, key confirmation, quota reservations, native daemon add/remove,
  config after ACK, kernel UDP budget/expiry, and inventory/restart reconciliation.
  A local real AWG test confirmed handshake/packets/remove/replay and both
  cutoffs. Without a trusted profile, admission is closed. The final live gate passed:
  real API → node ACK → Pixel handshake/staging egress; API revoke/cutoff;
  actual agent restart/reconciliation; transport loss → expiry/cutoff → recovery.
  In all three scenarios settlement and the normal network after disconnect were confirmed.
  Seven restored checks true, grant/timer/test APK/private files removed, endpoint,
  agent, forwarding, local API, and disposable PostgreSQL stopped.
  Evidence: `docs/evidence/i-012-staging-lifecycle.json` (passed=true).
  Code and launch — ADR-024, `docs/verification-i-012-admission.md`,
  `docs/runbooks/tunnel-lifecycle-staging.md`. The next issue is I-013 (separately
  assigned by the user on 2026-10-20, status below). Production/release gates remain.
- I-013: assigned 2026-10-20, technically accepted 2026-10-21 in bounded staging.
  Local 22 Flutter tests, 10 native unit tests, and physical native/API checks
  on the Pixel 8 Pro passed (Telegram delivery TEST FAKE ONLY).
  Lint and debug/unsigned release builds passed. The new Flutter client in `apps/client`
  uses the existing AWG adapter and the I-012 API; scope/limitations —
  ADR-025. The own Flutter/Android receipt `docs/evidence/i-013-staging.json`
  confirms handshake/egress, background/foreground, Disconnect/restore,
  remote revoke, expiry, settlement, and seven cleanup checks. Limitations:
  one node, three separate leases of up to 60s/1 MiB; DNS/IPv6 and Wi-Fi/mobile switching
  were not verified for the new APK; production/release gates are open.
  The recommended next issue is I-014; it was not implemented in I-013.
- I-014: implemented 2026-10-21 — the «Трафик и история» (Traffic and history) screen with exact integer bytes,
  granted/used/reserved/available, stable revision/cursor pagination,
  loading/empty/error/stale/retry, and RAM clearing on logout. ADR-026.
  On a physical Pixel 8 Pro through the local real API/PostgreSQL, an
  amount greater than 2^53, 20+5 history records, refresh, and a repeat without double credit were verified.
  Ledger events are synthetic, Telegram delivery TEST FAKE ONLY; the VPS was not changed.
  Receipt `docs/evidence/i-014-local-android.json` (`passed=true`),
  technically accepted in the local scope of ADR-026: 36 Flutter, 69 PostgreSQL,
  10 Android unit tests, lint/debug/unsigned release, and APK audit passed.
  Build and test results — `docs/verification-i-014.md`.
  This is quota/history acceptance, not a new verification of real VPN usage/egress.
  The next issue is I-015 (clicker + daily reward); it was not implemented in I-014.
- I-015: **accepted and closed 2026-10-21**. Server-only daily/clicker rules,
  UTC cap/count/cooldown/window, idempotency, atomic claim/ledger, and reward history.
  Backend: 417 passed, 2 skipped (separate I-012 staging/I-013 physical).
  Flutter: 38 passed, format/analyze; Android: 10 unit tests, lint clean,
  debug/release APK — passed. The final I-015 physical runner in a regular
  PowerShell: **1 passed, 418 deselected, 42.31s**; the real Pixel/API/PostgreSQL,
  reward history/replay, and DB reconciliation were confirmed. The temporary API/DB
  were stopped, ADB reverse removed, the regular APK restored. Receipts verified.
  Production rules are disabled; the Mini App rewards UI remains I-017.
  Next in the plan — I-016 (current status below). Deletion of temporary SDK/cache copies
  remains blocked by the platform and is not an open I-015 gate.
  Details: docs/verification-i-015.md, docs/evidence/i-015-acceptance.json,
  docs/runbooks/rewards.md.
- I-016: **implemented and technically accepted locally 2026-10-21** (ADR-028).
  Encrypted Fernet/pg_dump backup without a plaintext file, a single snapshot
  dump/manifest, restore into a new random DB, reconciliation of all public tables and
  ledger/rewards, cleanup on success/failure, and parallel drills. Read-only
  alert codes: DB/schema, node heartbeat, pending peer ACK, backup age/source/
  integrity, and local disk. Custom dump limit 32 MiB, local retention 7 days.
  **443 passed, 2 skipped**, including 26 I-016 tests on a real PostgreSQL 17.11;
  Ruff format/check, mypy (91 files), OpenAPI current. The disposable cluster was removed,
  closure of the TCP listener was verified. Receipt: docs/evidence/i-016-local-acceptance.json.
  The VPS/Android/VPN were not changed. **The deployment gate is open:** off-host storage,
  scheduler, independent delivery/supervisor, operational RPO/RTO, and privacy
  policy are not confirmed. The status does not mean working external alerts or
  production disaster recovery. Commands/manual steps: docs/runbooks/operations.md;
  full report: docs/verification-i-016.md. The next functional issue is I-017,
  not started in this task.

- I-017: **implemented locally 2026-10-21** (ADR-029). A React/TypeScript Mini App
  in the Soft Orbit style: exact bytes balance, daily/clicker via the I-015 API,
  devices read-only, the last 20 operations, light/dark, and logout.
  Server-side HMAC initData exchange, a five-minute window, durable replay protection,
  a shared account/initial grant with Android, and a restricted session without VPN capabilities.
  Local API/PostgreSQL and browser QA are confirmed; the real Telegram SDK,
  HTTPS, and menu button remain a separate manual gate. No publication/modification of the
  bot or VPS was performed. Production rewards are disabled. Partner tasks
  are not offered without a verifying provider/bot rights; referrals — I-018.
  Commands and actual results: docs/verification-i-017.md,
  docs/runbooks/telegram-mini-app.md. The next functional issue is I-018.

- I-018: **implemented and technically accepted locally 2026-10-22** (ADR-030).
  Campaigns/codes, immutable binding, server-side qualification by final
  observed counters and a matching applied lease debit. The bonus goes only to the inviter,
  once, within the campaign budget/cap/window; there are no default campaigns.
  Mini App: code/terms, binding, counts, cursor list, and an explicit check button.
  **498 passed, 3 skipped** in the full PostgreSQL regression; 5 client tests,
  TypeScript/Vite build, Ruff, mypy (103 files), OpenAPI current. A separate
  browser harness: **1 passed**, real local API/PostgreSQL and TEST ONLY
  Telegram/node data; binding, +17 bytes, counts, themes/390/320, and logout.
  Encrypted backup/restore with referral awards confirmed. The created disposable
  clusters were stopped/removed, ports 50535/18767 closed. The VPS/Android were not changed.
  **A public campaign is no-go:** economics/terms, retention/privacy/legal,
  support, and live Telegram/staging acceptance are needed. Farms of different accounts are not
  fully solved; accrual is pull-based, codes are entered manually, there are no automatic bans.
  Commands/manual gates: docs/runbooks/referrals.md; actual results:
  docs/verification-i-018.md, docs/evidence/i-018-local-acceptance.json.
  The next functional issue is I-019 (second node/failover); current status below.

- I-019: **accepted 2026-10-23: bounded same-provider node-failure drill (ADR-033)**.
  ADR-031: up to two private operator profiles, a heartbeat/runtime/capacity catalog,
  explicit node selection, and separate configs/agent credentials; one peer per node.
  Flutter shows the selection, reasons, and check time, and does not switch a live lease.
  Explicit owner recovery only after deadline+10s and a stale heartbeat: a full
  reservation debit with counters_lost, observed_bytes=NULL, and version fencing.
  This is not a measured usage/ACK and not a referral qualification. The old intent
  is completed before a new one; the stalled alert counts only current commands.
  Full PostgreSQL regression: 506 passed, 3 opt-in skipped; Flutter 44,
  native Android 10, analyze/lint/debug/unsigned release, and OpenAPI — passed.
  After the final grace fix, the affected set: 63 passed, 446 deselected.
  These are the results of the initial local checks;
  actual results: docs/verification-i-019.md and
  docs/evidence/i-019-local-acceptance.json.
  Full physical Android drill: 1 passed, 552 deselected, 238.22s; all 15 facts,
  fresh HTTPS egress B/A/A/B, cutoff/expiry without agent/API, two recoveries,
  return to A without reviving the old peer, restoration of the normal network.
  Ledger: four settlements, two full lost-counter debits, reserved_bytes=0.
  Both runtimes cleaned up, grants removed; the independent post-check passed.
  Receipts: docs/evidence/i-019-staging.json and i-019-session-post-cleanup.json.
  Final targeted synthetic: 43 Python, 49 Flutter; mypy 113 files passed.
  A production fleet and seamless/automatic failover are not this result.
  The I-020 payment sandbox has not started; it requires a separate legal gate.
  Owner clarification 2026-10-22, ADR-033: the current drill is permitted on two
  existing servers of the same provider (45.10.166.106 / 85.209.132.205).
  That was a separate same-provider gate. The read-only SSH preflight
  was performed; the pinned artifacts on B were installed and verified (ok=14, failed=0).
  The bounded runner completed the drill and revoked the temporary grants.
  Preparation history: docs/evidence/i-019-drill-preparation.json.

- I-019 independent-provider gate: **accepted 2026-11-01** per ADR-073/ADR-076.
  The physical Android drill on A `45.10.166.106` and B `150.241.99.227` passed:
  fresh egress A/B/A/B, expiry/recovery/cutoff, four settlements,
  `reserved_bytes=0`, cleanup of both hosts, and removal of grants. Different origin ASNs
  confirm a limited provider separation; full upstream independence
  and automatic failover were not verified. Receipt:
  `docs/evidence/i-019-attempt-2df6394bc51e41e3b1fb066a1ba7e395.json`.

### Separate owner UI task — Android Soft Orbit, 2026-10-22

The Android interface was brought in line with the provided Soft Orbit reference: a single theme,
tabs VPN/Трафик/Профиль (VPN/Traffic/Profile), a separate node selection, an orbital card, and history.
Telegram/Mini App and the tunnel controller were not changed. This is a UI iteration on top of I-019,
not the closure of the two-node live gate. Checks and limitations:
`docs/verification-android-soft-orbit.md`.

### Separate owner task — Telegram Serverless bot, 2026-10-22

Telegram Serverless bot navigation
is implemented locally (`apps/telegram-bot`, ADR-032), 44 tests passed. Public
links, hosted acceptance, and a protected auth relay are open. The webhook of the current Android auth bot
must not be switched before relay acceptance. Facts:
`docs/verification-telegram-bot.md`; this is not a new completion status for I-017/I-019.

After explicit owner approval, the menu bot was published on 2026-10-22:
@DetourVpnBot, revision 2, 7 modules, two operational tables/indexes, and six
private-chat commands. The hosted V8/SQLite probe and webhook sync are confirmed.
OTP relay, public links, and user-chat `/start` acceptance are still open;
receipt `docs/evidence/telegram-bot-deployment.json`.

## 13. How to work with Codex on each issue

### Closed free operation — assignment 2026-10-23

The owner authorized sequential implementation of I-021–I-028 without payments.
Gaps, dependencies, and acceptance: `docs/issues/I-021.md`.
Current point: **I-024 implemented and accepted locally**, see
`docs/issues/I-024.md`, `docs/verification-i-024.md`, and `docs/runbooks/peer-windows-v3.md`.
Backend/agent/Linux: 719 passed, 5 opt-in skipped; two real isolated
AWG tunnels, 105 seconds of transfer, four renewals on each, and crash recovery.
This is not a VPS rollout and not physical/daily acceptance; they remain I-028.
I-025 was assigned by the owner on 2026-10-23: **implemented and verified locally**;
the phone will be connected later. Final 70 Flutter tests, 17 native unit tests,
format/analyze/lint/debug/unsigned release — passed. The key/refresh are stored
encrypted via Android Keystore, v3 renewal requires an active ACK, native
monotonic deadline/fencing; local split apps, IPv4 DNS, and an IPv6 blocking policy.
Physical native Keystore/corruption/process/permission-denial checks passed
(10 phases, 11 facts); the local Flutter/API/PostgreSQL session/refresh/logout/lost-reply
passed (1 passed, 68.84s). The real v3 tunnel/lifecycle/Doze/network/DNS/IPv6/split
routing remains open: neither node has v3 slots. There were no remote changes.
Details: `docs/verification-i-025-physical.md`.
See `docs/issues/I-025.md`, `docs/verification-i-025.md`, ADR-040, and the local receipt.
One attempt was stopped by C: full; the final repeat in the existing snapshot passed.
I-026: the owner-only live Mini App → Android OTP acceptance passed 2026-10-26;
friends and the VPN data plane are not accepted by this. I-027: on 2026-10-26 a
backup-only service was deployed on the beta API; the encrypted archive was downloaded to the PC and restored from it into a
disposable PostgreSQL. The Windows task is registered for 07:00 daily, the first
scheduled run is expected 2026-10-27. S3, an independent monitor, and automatic deletion of
archives are deferred. I-030: separate control-plane/node
  templates, invite gate, closed AWG bootstrap, and the scoped beta packet path passed
  local verification; the TEST ONLY AWG client and HTTP flow were confirmed in disposable
  WSL namespaces. The beta host, provider firewall, reboot recovery, and hosted deployment
  are not accepted. See
`docs/issues/I-027.md` and `docs/issues/I-030.md`.
Update 2026-10-26 to I-030: two new isolated beta VMs passed a partial
hosted acceptance. The API/DB survived a container restart and reboot, staying
loopback-only. A separate VPN node passed a closed slot start/stop/start and reboot
recovery; the gate is closed, the agent has no credential, provider ingress/client traffic
was not verified. On the API VM a trusted short-lived IP TLS was deployed with a challenge-only
HTTP, a renewal dry-run, and a custom timer; HTTPS currently answers 404 and the API is not
open. Independent certificate alerting and I-027/I-028 acceptance are open; the I-026
owner-only live OTP gate is accepted, without a public launch.
I-030 remains open; receipts are in `docs/evidence/`.
I-029: a headless Always-on lease revalidation with a protected
relay is implemented locally; the debug manifest enables support only for controlled acceptance,
the release manifest disables it. The Android callback/Lockdown and recovery are not yet
accepted physically; I-029 and the kill-switch claim remain open. See
`docs/verification-i-029.md`.
The owner on 2026-10-23 authorized a bounded temporary I-028
staging on A and physical checks: preparation proceeds per ADR-041 and
`docs/runbooks/v3-android-staging.md`; local receipts do not mean remote
acceptance. On 2026-10-24 a real Android I-028 scenario was executed: IPv4/DNS,
renewals up to generation 7, split routing, Disconnect/remote revoke, loss of
API/agent with closure by deadline, and recovery; 4 189 203 measured bytes
reconciled in the ledger. The common runner saved false because of an error checking the already
stopped timer. The fix was separately verified on A with a successful cleanup
and an independent post-check; these are two separate receipts, not one successful run.
At that time Doze, network change, system revoke, and other I-028 cases still
remained open; the entries below update them via separate receipts.
A new single main I-028 run passed on 2026-10-24: 1 passed in 474.23s,
including Disconnect with a delayed renewal/real ACK and the fixed cleanup.
13 receipts, 1 810 465 measured bytes, zero reservations/unknown/open/violations;
`docs/evidence/i-028-main-success.json`. The old failed receipts are preserved.
A separate NetworkRevoke run passed on 2026-10-24: 1 passed in 277.73s,
real Wi-Fi/cellular/loss of both networks/return to Wi-Fi and system
permission revoke without self-connecting; 5 receipts, 31 799 704 bytes,
zero reservations/unknown/open/violations, full cleanup of A/Pixel and an independent
check. `docs/evidence/i-028-network-revoke-success.json`; the earlier
errors are preserved. The Lifecycle runner recorded background, screen-off, and
forced Doze, but the whole run failed due to loss of ADB/USB during recovery;
a separate follow-up confirmed the subsequent recovery of the phone.
At the time of this entry, natural sleep, a full successful lifecycle run,
a comprehensive DNS/IPv6 leak capture, two physical devices, and an independent
provider remained mandatory. The next entry below records the subsequent
Lifecycle and NaturalSleep passes; the open gates are listed in the matrix. I-028
is not closed.
Continuation 2026-10-25: a separate NaturalSleep run confirmed a new unforced
deep-idle at 412 seconds without USB and zero live slots/peers on A during sleep,
but this specific run finished `passed=false` before checking native down and
a new egress after wake-up; an early attempt stopped at Play Protect.
A full repeat
Lifecycle passed with one receipt in 659.47s: background, screen-off, forced
Doze, Wi-Fi/mobile/loss/Wi-Fi, system revoke, 20 exact receipts for
1 172 946 bytes, full cleanup of the phone and A. At that time NaturalSleep,
a comprehensive DNS/IPv6 leak capture, two physical devices, and an independent
provider remained open; a later entry closes NaturalSleep.
Staging does not mean production-ready.
The NaturalSleep harness was hardened with a bounded app-private lifecycle/native timeline;
the source-bound Android test build passed. The staging A install preflight stopped
at the minimum free disk before creating VPN resources; receipt in I-028.
See `docs/verification-i-028.md`. I-020 payment
is not activated.

Update 2026-10-25: the source-bound NaturalSleep passed on one Pixel in
11:37. Confirmed: 392 seconds without USB, natural deep-idle, native tunnel
down after wake-up, new staging egress, and window recovery; 29 662 774
bytes reconciled, phone/A cleanup and an independent check passed. Receipt:
`docs/evidence/i-028-natural-sleep-staging-20261025T192620Z-286a330b.json`.
This closes only NaturalSleep. I-028 still has a full packet-level
DNS/IPv6 audit, two physical devices, and an independent provider failure;
I-028 is not closed. The temporary staging A is cleaned up, but its free space is below the threshold for
reinstallation; it is not a beta site for friends.

Update 2026-10-29: the two-device I-028 staging acceptance passed,
receipt `docs/evidence/i-028-two-device-staging-20261029T101845Z-b0085c2a.json`.
The Pixel 8 Pro on cellular and the 25040RP0AG on Wi-Fi used different windows/slots and
real staging egress; cross-owner revoke isolation, B continuing after
A's disconnect, exact traffic reconciliation, and cleanup passed. The false failed receipt
of the previous run is preserved unchanged. I-028 is still open for the full
packet-level DNS/IPv6 audit and independent provider failure; the independent
site is not yet chosen/authorized. The staging result does not mean readiness
for production.

Update 2026-10-26, I-026: on his own Android device the owner
completed the live Telegram Mini App → OTP → Android sign-in happy path on a separate
beta bot; Telegram worked through a Sota Connect enabled by the owner. The Telegram
webview and the authorized Android cabinet were observed; OTP and `initData` were not
stored. A privacy-minimal receipt was deployed to the beta API; migration 0017,
backup, readiness/health, and the Mini App route were verified. At the time of that entry the hosted
OTP receipt, replay/duplicate rejection, and pairing were not yet confirmed.
Addition 2026-10-26: the owner completed a debug-only live probe; the UI reported that the
server rejected an exact OTP repeat. A read-only reconciliation of the beta API showed one consumed
attempt with `api_accepted` and a delivery time, one native session with an exact
match of `consumed_at`/`created_at`, and one active device row for the account.
Raw IDs, OTP, and `initData` were not read; the current session did not get a `device_id`,
since the VPN was not started. I-026 is accepted only for the owner-only login/replay scope;
friends and the data plane were not verified. Details: `docs/issues/I-026.md` and
`docs/evidence/i-026-beta-live-delivery-receipt-20261026.json`.
Current status as of 2026-10-29: the manual transfer of the encrypted copy and the restore of
I-027 passed. After an access error the scheduled task was fixed: it uses
the current interactive user with limited rights, and the action and principal
were verified by a repeated idempotent registration. The fixed scheduled run is not yet
confirmed. See `docs/evidence/i-027-beta-task-principal-20261029.json`. In I-028 the separate
two-device physical criterion passed, but the full DNS/IPv6 packet audit and
the independent-provider failure remain open per the acceptance matrix. These leftovers
must not be replaced by old wording about an unverified second device or
declared complete. See `docs/beta-readiness-20261029.md` and the I-026
evidence: `docs/evidence/i-026-live-miniapp-otp-20261026.json`.

1. Pass the context (`TECHNICAL_SPEC.md`, ADR, `AGENTS.md`) and **one** issue.
2. Ask for 3–8 concrete steps, the affected files, risks, tests; without an immediate huge change.
3. Assign implementation exclusively of this issue in a separate branch.
4. Run the formatter, linter, mypy/pyright, and tests; on Android — Gradle/Flutter tests.
5. Codex must return: what changed, which commands were run, results, unresolved questions, and a list of manual checks.
6. Verify the diff yourself (especially IAM, keys, SQL, payments), then merge.
7. After each stage, go through the DoD and update ROADMAP and ADR.

## 14. Risk matrix

| Risk | Probability / impact | Tactic |
|---|---|---|
| Native VPN integration is harder than the Flutter UI | High / high | Tunnel prototype before a large UI |
| Hard blocking of nodes | High / high | Diversification, honest statuses, R&D without a guarantee |
| Uncertainty of advertising law | High / high | Legal gate before ad purchases |
| Payment provider blocking the seller | Medium / high | Due diligence, do not hide the business model from the provider |
| Clicker abuse | High / medium | Server ledger, daily cap, idempotency, antiabuse |
| Cost of free traffic | High / high | Quotas, budget limits, unit economics |
| License incompatibility | Medium / high | SBOM, licence check per library |
| DDoS of a node/panel | Medium / high | L3/L4 hosting mitigation, API WAF, redundancy |
| Leak of user data | Medium / very high | Data minimization, secret lifecycle, external audit |
| AI-generated false sense of completeness | High / high | Acceptance by measured tests, no mock in release |

## 15. Nearest decision point

The first 3 technical unknowns:
1. Which native Android VPN library can be used safely and in a licence-compliant way with Flutter?
2. How to organize counters and quota enforcement on a specific engine without double debit on restart?
3. Which sign-in method provides reliable identity without excessive personal data and complies with platform rules?

Do not start attractive gamification until the base backend ledger and a working tunnel are complete.

Historical I-019 attempt `docs/evidence/i-019-attempt-5b914fde876e4c0e866bcce4729835f8.json`
failed at B reconnect with four `dns` probe results and no fresh handshake
despite the DNS-free trigger. The later successful attempt is recorded above;
this earlier failure remains in the evidence archive for diagnosis history.

Earlier I-019 attempt `docs/evidence/i-019-attempt-d16cc96c0cd54f59b940d98e4331bb41.json`
failed at B reconnect without a fresh handshake. Its `other` probe result was
the sanitizer's lost `dns` category; the DNS/handshake cause remains unknown.
The sanitizer now retains all fixed categories (six diagnostic tests passed).
Operator recovery and read-only checks confirmed B stopped and forwarding=0;
independent-provider acceptance remains open.

I-028 owner-scoped bounded staging disposition (updated 2026-11-01): per ADR-071, the
personal VPN profile uses IPv4 and blocks included IPv6. The source-bound Egress
receipt and correlated capture passed this bounded fixed-socket check. Per
ADR-072, no further broad DNS/IPv6 audit is required to finish this personal
staging checkpoint; this does not assert device-wide leak freedom or satisfy
broader product-release requirements. The bounded independent-provider
criterion passed on 2026-11-01 in
`docs/evidence/i-019-attempt-2df6394bc51e41e3b1fb066a1ba7e395.json`: 15/15
facts, fresh A/B egress, both recovery paths, four settlements, two full
lost-counter debits, zero reserved bytes, and cleanup on both hosts. The report
cap defect in earlier failed attempts was raised from 16 to 32 and
regression-tested. This verifies distinct origin ASNs for the bounded run, not
full upstream independence or automatic failover. ADR-072 keeps the broad
packet audit deferred for this personal checkpoint; neither acceptance implies
device-wide leak freedom or broader product-release readiness. The separate
physical ADR-070 established-session status observation remains open. Earlier
failed receipts remain preserved. Evidence:
`docs/evidence/i-019-attempt-d17af2fd6b494e42a769f2ab53493771.json`,
`docs/evidence/i-019-attempt-4817ce4af80647f492b4e9bcc1f2d4ba.json`,
`docs/evidence/i-019-attempt-627387de1c87477bac1f3f71de8f5ca0.json`,
`docs/evidence/i-019-attempt-5dcc929ff4cd47ae8fe3b58fb794773b.json`,
`docs/evidence/i-019-staging.json`,
`docs/evidence/i-019-b-reuse-diagnostic.json`, and
`docs/evidence/i-019-session-cleanup.json`.

- Closed beta: **ADR-090 proposed 2026-11-08** (boundaries of 5–20 testers, stop criteria,
  go/no-go on the open items of `docs/beta-readiness-20261029.md`). Quota 512 MiB, duration
  4 weeks, rewards disabled — agreed by the owner "for now"; the VPS budget is open
  (plan and traffic limit). This is a definition, not launch readiness.
