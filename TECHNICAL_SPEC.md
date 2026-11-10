# VPN Service: Technical Specification (PRD + SRS)

**Version:** 0.2  
**Date:** 2026-10-18  
**Status:** draft for discussion and implementation via Codex  
**Name:** DetourVpn (approved by the owner on 2026-10-21)

Change 0.2: per the owner's direct clarification of 2026-10-18, the primary login
is by a one-time code generated in the Telegram bot. AUTH and the login-start
contract are refined; the earlier email/OIDC choice is no longer the current plan.
Details and further proposed technical parameters: `docs/adr/001-authentication.md`.

> This is an engineering specification, not a legal opinion and not a guarantee of bypassing network restrictions. All legal, licensing, and payment decisions must be verified for the country of operation and the distribution market before public launch.

## 1. Product vision

Create an independent VPN service with its own client application, commercial plans, a free traffic quota for useful activity, and verified transport implementations. The project must be modular: replacing a node, transport, reward system, or payment provider must not require rewriting the core application.

### 1.1. Product principles
- **Security and trust:** do not intercept user sessions, do not sell network activity, do not use VPN traffic for advertising monetization.
- **Transparency:** honestly describe limitations, data retention, connection regions, pricing, and traffic reward rules.
- **Resilience:** failure of a specific node or of the management API must not automatically break an already established connection.
- **Extensibility:** new network engines are added via adapters with separate compatibility tests.
- **Measurability:** decisions are made after measuring the cost of 1 GB, user retention, anti-fraud, and connection quality.
- **Legal prudence:** the brand is separated from personal accounts; legal requirements for registration, payments, and reporting are not circumvented.

### 1.2. Audience
- Private Android users who need a protected connection and a simple interface.
- Users who choose a limited free quota earned through activity.
- Paid-subscription users who value stability and the absence of advertising tasks.

### 1.3. What we do **not** promise
- Guaranteed internet access under a strict allowlist when the external IP of the VPN node is unreachable.
- 100% immunity to DPI, IP blocking, or DDoS.
- Absolute anonymity of the owner with respect to providers, banks, and competent authorities.
- A "no logs" policy without an architectural and independent audit.

## 2. MVP and future release boundaries

### MVP (P0)
1. Android client: registration, login, home screen, traffic balance, node catalog, connect/disconnect, clear errors.
2. Backend: users, devices, authentication, node catalog, configurations, aggregated traffic, plan limits.
3. One working VPN node with a verified engine and manual node switching on failure.
4. Initial free quota + daily reward + clicker with server-side limits.
5. Admin interface for viewing node status and adjusting user limits with an audit of changes.
6. Monitoring, backups, secret encryption, automated tests, and minimal documentation.
7. Closed testing; no public payments and no ad purchasing until legal review.

### Release 1 (P1)
- Telegram Mini App, verifiable tasks, promo codes, referrals.
- Integration of real payments and subscriptions **only after** legal and payment approval.
- Second independent node; manual and automatic switching with quality awareness.
- Several tunnel types through the same adapter contract.
- Quota/priority policy; user support requests and account deletion.

### Release 2 (P2)
- Windows/Linux, then iOS after confirmation of rights, licenses, and App Store requirements.
- Extended transport tests; research into behavior in restricted networks without promises of universal bypass.
- Advanced gamification: levels, achievements, seasonal events, partner tasks.
- Dashboard of CAC, LTV, retention, and gross margin metrics.

### Out of MVP scope
- Custom cryptography; a nationwide advertising campaign; guaranteed allowlist bypass; the Kubernetes container orchestrator; specialized enterprise-grade protection; a proprietary financial intermediary/payment system.

## 3. Architecture

```text
 [Android Flutter]           [Telegram Mini App / later]
       |                                   |
       +------- HTTPS / REST --------------+
                       |
                 [API Gateway]
                       |
               [FastAPI control plane] ---- [PostgreSQL]
                |   |        |              [Redis]
                |   |        +-- Rewards / Billing (ledger)
                |   +----------- Provisioning + Quota service
                +--------------- Node registry / signed status
                       |
            mTLS / authenticated agent channel
                       |
          +------------+------------+
          |                         |
     [VPN node A]                [VPN node B]
     tunnel engine              tunnel engine
     local agent                local agent
          ^                         ^
          +----- encrypted user data tunnels -- [Android OS VPN/TUN]
```

**Critical:** user tunnel traffic goes directly between the device and the VPN node. The backend is not on the data path and does not receive the history of visited sites. The web API, the admin interface, and node management access have separate security policies.

### 3.1. Technology choices
- Client: **Flutter/Dart**, native Android part **Kotlin + VpnService**, integration of a proven tunnel SDK/library in compliance with licenses; Flutter does not replace the native VPN engine.
- Backend: **Python 3.x + FastAPI + Pydantic + SQLAlchemy 2 + Alembic**; pin versions with lock files after compatibility verification.
- PostgreSQL: ledger-like operation journal, accounts, nodes, limits, payments.
- Redis: rate limits, short-operation locks, task queues; **not** the source of truth for balance.
- Background tasks: a lightweight worker; choose Celery/RQ/other after measurements; in the MVP one clear solution is sufficient.
- Infra: Linux LTS on a VPS, Docker Compose (API/DB/metrics), Ansible/Terraform as needed. Nodes may run the VPN engine on the host if necessary (not everything has to be in Docker).
- CI: GitHub Actions (lint, type check, tests, dependency audit, build verification), branches and code review.
- Observability: Prometheus/Grafana, minimized structured logs, alerts.

### 3.2. VPN engines and compatibility
- `TunnelEngineAdapter` with methods `validate_capabilities`, `provision_peer`, `revoke_peer`, `read_counters`, `health`, `rotate_config`.
- For Android, the client implements a compatible `TunnelProvider` with the contract `connect`, `disconnect`, `get_status`, `network_changed`.
- Baseline candidate: AmneziaWG; **choose the version after verifying stability, supported client libraries, and the compatibility matrix**. Do not default to AWG 2.0: the documentation already describes AWG 3.1.
- Additional candidate: Xray-core, MPL-2.0. Investigate whether a separate TUN provider / userspace backend is needed, and the implications for battery and licensing.
- Do not copy the ready-made AmneziaVPN client without accounting for GPL-3.0, including the requirements when distributing modifications; perform a separate license review of every embedded library.
- The matrix `engine × version × OS × client version × IPv4/IPv6 × DNS × suspend/resume` is mandatory.

### 3.3. Node management protocol
- The node agent either establishes an outgoing authenticated connection to the management server or accepts mTLS commands over a strictly restricted channel.
- The Node Agent may issue/revoke configurations and expose only aggregated counters and node state; it does not accept arbitrary shell commands from the public API.
- All operations have an `operation_id`, a TTL, and idempotency; administrative changes are recorded in the audit log.
- If the management API is lost, active sessions continue to work as far as the transport and the node allow.

## 4. Roles and use cases

### Roles
- **Guest:** views the description of plans/features, without connecting.
- **Free user:** receives the initial quota; VPN and game tasks within the established limits.
- **Premium user:** commercial limits/priority without advertising tasks (after payments are introduced).
- **Operator:** node status, support requests, limited actions without access to secrets.
- **Administrator:** deployment and configuration issuance under RBAC + MFA, with full audit.

### Main user stories
- US-01: After registration the user receives the starting quota **strictly once**.
- US-02: After pressing «Подключить» (Connect), the user sees `connecting/connected/failed`, the region, and the current status.
- US-03: When there is no free quota left, the connection is stopped/restricted according to the published policy, with an acceptable measurement delay.
- US-04: Clicks earn rewards while the daily limit remains; resending a request does not yield a second reward.
- US-05: The once-a-day bonus is available only after server-side verification.
- US-06: Changing the region does not reset the balance and does not duplicate the device.
- US-07: When a node is down, the interface explains the reason and offers available alternatives.
- US-08: The user can sign out of all devices, delete the account, and request information about stored data.
- US-09: The operator sees the infrastructure state but not the user's internet history.
- US-10: For payment scenarios, access changes only after a verified webhook from the payment provider.

## 5. Functional requirements

### AUTH
- AUTH-01: In the MVP, a one-time code generated by our Telegram bot service and delivered to the user in a private chat; the user enters the code in the app. The account is linked to the Telegram user ID from a trusted Bot API Update. Email/OIDC is not the selected method. An independent fallback login/recovery is not yet defined and requires a separate decision.
- AUTH-02: Passwords (if any) must be stored using Argon2id; MFA is mandatory for administrators.
- AUTH-03: Sessions and refresh tokens are revocable; rate limiting on login and recovery; protection against enumeration.
- AUTH-04: Devices have a separate `device_id` and tunnel keys; the ban on sharing configurations in the commercial version must be designed through device/session limits, without invasive fingerprinting.
- AUTH-05: The code is time-limited, single-use, bound to the login attempt and the Telegram identity; brute-force limiting, safe storage, and atomic redemption are mandatory. The exact TTL/format/limits are determined by ADR-001; a short code without binding to the attempt is not accepted.
- AUTH-06: Code issuance is available only to the trusted bot adapter. Verify the source of Updates and the idempotency of update_id; do not accept a Telegram ID from the public client, a username, or forwarded content. The code, the bot token, and the client verifier must not appear in logs. The Mini App is not required for login itself.

### VPN
- VPN-01: A list of available regions and nodes with an honest status indicator (do not promise "available at any operator").
- VPN-02: Create/revoke a peer on a node without exposing the device's private key in the API; where possible, the private key is generated locally.
- VPN-03: Issue a configuration only to the device owner, with access checks and expiry.
- VPN-04: Full tunnel and correct DNS by default; protection against DNS/IPv6 leaks is tested on real networks. Do not include a "kill switch" in marketing until the OS behavior has been verified.
- VPN-05: Correctly handle Wi-Fi/LTE switching, sleep/resume, loss of the VpnService permission, and region switching.
- VPN-06: Outgoing/incoming byte counters are collected from the node; fix the billing rule (for example, `rx + tx`); do not use client readings as the source of truth.
- VPN-07: When the API is unavailable, the operation of an already configured tunnel does not depend on the clicker/payments interface.

### TRAFFIC / QUOTA
- Q-01: The number of bytes as `bytes BIGINT` is the only source of truth for volume accounting; conversion to MB/GB only on display.
- Q-02: Traffic accounting is idempotent consumption of monotonic node counters. On a counter restart a new `counter_epoch` applies; negative deltas must not be debited.
- Q-03: For each measurement batch store `node_id/peer_id/epoch/sequence/timestamp/counters`; keep a separate debit journal.
- Q-04: Accounting must account for metric delivery delays and explicitly document the enforcement error margin (a hard cutoff at a single byte is not guaranteed in a distributed network).
- Q-05: A safe reserve as the balance approaches zero; limits on usage and an upper bound on the permitted over-consumed quota.
- Q-06: An operator adjustment is a separate ledger operation, not a direct replacement of the `balance` field.

### REWARDS / GAMIFICATION
- G-01: The reward rule lives on the server: `reward_type`, `reward_bytes`, `daily_cap`, `cooldown`, `eligibility`, `campaign_window`.
- G-02: One click = a fixed quota (approximately 1–2 MB), at most X clicks and Y MB per day; the parameters are configurable, not hardcoded.
- G-03: Rewards are processed transactionally; unique `idempotency_key` + `user_id`, ledger credit only once.
- G-04: The daily bonus is determined by the server calendar day with a documented time zone.
- G-05: Tasks for joining a Telegram channel are credited only if membership can be reliably verified; the checks require appropriate bot permissions.
- G-06: Do not grant a reward merely for pressing the «Я подписался» (I have subscribed) button without verification if the advertiser expects a confirmed participant.
- G-07: The referral bonus only after a defined valid event; protection against self-referrals and limiting the damage from account farms. For I-018 (ADR-030): a confirmed active account + a completed managed tunnel created after binding, with positive observed counters and a corresponding applied quota debit. Only to the inviter, once, within the campaign window/cap/budget. There are no campaigns by default; the check is requested manually in the Mini App.
- G-08: Transparent publication of task terms; advertising is labeled where required; do not create artificially paid subscriptions contrary to the rules of platforms and advertisers.
- G-09: On anomalies, limit credits and review manually; do not apply unexplained permanent bans.

### PAYMENTS (after the legal gate)
- P-01: A `PaymentProvider` abstraction for one integrated processor.
- P-02: Verify the webhook signature, replay protection, uniqueness of provider_transaction_id; record the status: created, pending, paid, failed, refunded, chargeback.
- P-03: The subscription and access rights are defined by a separate entitlements table; do not consider the receipt of money as finally confirmed by a redirect to the success URL.
- P-04: Handling of refunds, errors, renewal cancellation, tax documents, and support.
- P-05: Do not store bank card numbers; use tokens of an authorized provider.
- P-06: App Store/Google Play and Telegram requirements for selling digital services must be checked before integration.

### ADMIN
- A-01: Separate roles under the principle of least privilege, and MFA.
- A-02: List of nodes, error rate, disk exhaustion, CPU load, traffic volume, queue of failed issuances.
- A-03: Changing quotas and plans requires a reason and an audit.
- A-04: Export of aggregated metrics without PII; emergency shutdown of issuing new configurations.

## 6. Preliminary data model

| Table | Key fields |
|---|---|
| `users` | id, status, created_at, verified_at, locale |
| `auth_identities` | id, user_id, provider, provider_subject, verified_at |
| `devices` | id, user_id, platform, public_key, created_at, revoked_at |
| `regions` | id, code, label, enabled |
| `nodes` | id, region_id, provider, engine, engine_version, status, capacity, last_heartbeat_at |
| `peers` | id, node_id, device_id, remote_identifier, state, created_at, revoked_at |
| `usage_samples` | id, node_id, peer_id, epoch, seq, rx_total_bytes, tx_total_bytes, received_at |
| `quota_ledger` | id, user_id, kind, delta_bytes, source_type, source_id, idempotency_key, created_at |
| `reward_rules` | id, type, reward_bytes, daily_cap_bytes, cooldown_seconds, enabled |
| `reward_claims` | id, user_id, rule_id, task_id, status, idempotency_key, created_at |
| `tasks` | id, partner_id, type, requirements, state, start_at, end_at |
| `referral_campaigns` | id, reward_bytes, budget_bytes, inviter_cap, enabled, starts_at, ends_at |
| `referral_codes` | user_id, code (random public code) |
| `referrals` | id, inviter_id, invitee_id (unique), campaign_id, reward_bytes snapshot, idempotency_key, created_at |
| `referral_awards` | referral_id (unique), user_id, campaign_id, operation_id, idempotency_key, reward_bytes, balance_after_bytes, created_at |
| `plans` | id, code, period, quota_bytes, device_limit, price_minor, currency |
| `subscriptions` | id, user_id, plan_id, status, current_period_end, provider_subscription_id |
| `payments` | id, user_id, provider, external_id, amount_minor, currency, state, created_at |
| `audit_events` | id, actor_id, action, resource_id, reason, created_at |

Requirements: UUID identifiers, Alembic migrations, uniqueness constraints, indexes on `user_id` and time period, a foreign key for all references, concurrent balance operations in transactions. Do not perform a debit on a simple UI update.

## 7. API v1: initial contracts

Base prefix: `/api/v1`; all user methods over HTTPS. Methods with user data require authentication and resource ownership control; login start/verification are available without an existing session but are protected by pairing, limits, and validation according to ADR-001. All returned errors: `{ "error": {"code": "...", "message": "...", "request_id": "..."} }`.

| Method | Route | Purpose |
|---|---|---|
| POST | `/auth/start` | Create a login attempt and a bot link; does not issue a code for an arbitrary Telegram ID |
| POST | `/auth/verify` | Verify the code from the bot and the binding to the attempt, atomically create a session |
| POST | `/auth/mini-app` | I-017: one-time exchange of verified Telegram initData for a limited cabinet session (ADR-029); disabled by default |
| POST | `/auth/refresh` | Session refresh |
| GET | `/me` | Profile/status |
| DELETE | `/me` | Account deletion request |
| GET | `/regions` | List of regions |
| GET | `/nodes` | Permitted nodes and their current status |
| POST | `/devices` | Device and public key registration |
| POST | `/tunnels` | Issue a configuration for a device and node |
| DELETE | `/tunnels/{id}` | Revoke a configuration |
| GET | `/quota` | Granted, used, available in bytes |
| GET | `/quota/history` | Journal of credits/consumption |
| GET | `/rewards/rules` | Active game rules |
| POST | `/rewards/claim` | Credit attempt: only after server-side verification |
| GET | `/tasks` | Available tasks |
| POST | `/tasks/{id}/verify` | Completion check and crediting |
| GET | `/referrals` | Own counters, records, and campaign terms; cursor pagination |
| POST | `/referrals/code` | Create or re-retrieve own public code |
| POST | `/referrals/resolve` | Immutable binding of the invitation |
| POST | `/referrals/claim` | Server-side event verification and atomic bonus to the inviter |
| GET | `/health/public` | Safe aggregated system status |

Administrative and agent endpoints are located separately; listing them in the public API is prohibited. Contracts must be formalized with OpenAPI + error schemas + versioning.

### 7.1. Example balance response
```json
{
  "granted_bytes": 2097152000,
  "used_bytes": 734003200,
  "available_bytes": 1363148800,
  "updated_at": "2026-10-15T10:00:00Z",
  "usage_estimation_delay_seconds": 60
}
```

## 8. Android application requirements

### 8.1. Screen map
1. Welcome / onboarding: what the VPN does, the required OS permission, a link to the data policy.
2. Authorization / access recovery.
3. Main screen: a large Connect button; status, selected region, remaining traffic, error + solution.
4. Region selection: availability, status, and manual choice; latency only if actually measured.
5. Free traffic: clicker, daily rewards, verifiable tasks.
6. History of credits and expenses; separate accounting for transparency.
7. Plans, subscriptions, and payments (hidden until the legal/payment gate).
8. Settings: launch, notifications, diagnostics, support, account deletion.

### 8.2. UX principles
- Visual direction Soft Orbit: plum surfaces, soft route lines,
  rounded cards, calm typography, light/dark. The default accent is coral;
  the theme palette allows lavender, turquoise, gold. Error/warning colors are independent
  of the accent; text contrast is mandatory. Values from the reference are demonstrative.
- The app manages the VPN, regions, status, traffic, and settings. The Mini App
  is a separate interface for the cabinet/balance/bonuses/tasks/referrals/devices/support
  in I-017. I-015 prepares server-side rewards; the regular client only displays them
  in the traffic history. A bonuses screen is not added to the regular app.
- Statuses `disconnected`, `connecting`, `connected`, `reconnecting`, `failed`.
- Request the `VpnService` permission only after an explanation and a user action; do not mislead.
- Show realistic error causes: node unavailability, expired access, network error, VPN conflict, missing OS permission.
- On an API failure show the stored balance with a note of how stale the update is, and do not invent a current value.
- Do not request contacts/geolocation permissions without real necessity.
- Dark and light themes; accessibility: font scaling, contrast, screen-reader labels.

### 8.3. Native VPN part
- A Kotlin module on top of the Android `VpnService` with explicit lifecycle management.
- Flutter calls the native layer through a platform channel/FFI adapter; no secret is printed to the debug log.
- Integration of the standard system VPN permission.
- Background operation, network, IPv6, and DNS are verified on the target Android versions; do not substitute a working connection with an animation.

## 9. Threat model / information security

### Assets
- Accounts, tokens, service keys, tunnel configurations, balances/payments, VPS access, brand reputation.

### Threats and countermeasures
| Threat | Control |
|---|---|
| Leakage of tokens/API keys | Secret manager, no secrets in Git/logs, rotation, short-lived tokens |
| Forged credits via the clicker | Server time/rules, transactions, idempotency, limits, anti-bot signals |
| Replay of a payment webhook | Signature, timestamp, unique transaction, idempotency |
| Node taken out of service | Different providers, standby, IaC, health checks, replacement plan |
| DDoS against the API | CDN/WAF, rate limits, origin lockdown, resource quotas |
| DDoS against VPN UDP/TCP | L3/L4 filtering on the provider side, capacity planning, standby nodes |
| Database access | Network segmentation, least privilege, backup encryption, admin MFA |
| DNS / IPv6 leaks | E2E tests and safe behavior on tunnel drop |
| Tracking/harassment of the operator | Public brand privacy, separation of work accounts; compliance with mandatory identification/payment requirements |
| Abuse of the service | Transparent rules, resource limits, support and abuse handling |

### Data privacy
- Store only what is necessary for operation and billing: account identifier, device configuration (without private keys where possible), aggregated bytes, financial transactions, diagnostic events.
- **Do not collect URL history, DNS queries, or tunnel content** for analytics/advertising.
- Temporary security logs without IPs by default, if possible; if an IP is necessary for abuse protection, an explicit retention period/access/masking and a legal basis are required.
- Separate application telemetry from user traffic routing.
- Retention periods and data deletion are approved after analysis of the applicable law; provide for deletion/export at the model level.
- Do not make claims such as "100% anonymous", "we leave no traces", "no one will be able to identify".

## 10. Operational security, observability, recovery

- Separate secret spaces for staging and production.
- VPS access via SSH keys, without permanent root access from CI, system updates, minimization of open ports.
- Codex or CI must not be given access to working infrastructure at an early stage; first a test environment, then limited scoped credentials.
- Separate the control plane and the data plane. Do not host the admin panel openly without MFA/VPN/ACL.
- PostgreSQL backups with encryption and restore verification; a separate storage provider is preferable.
- Runbooks: API unavailable; node unavailable; keys leaked; ledger corrupted; traffic volume exceeded; DDoS.
- Metrics: successful connections, error rate, handshake latency (if available), bandwidth/disk capacity of nodes, usage ingestion lag, share of erroneous rewards/payment events.
- Do not confuse CDN protection of the API with protection of VPN nodes at the L3/L4 level.

## 11. Initial quality indicators (SLOs: targets, not promises)

| Indicator | MVP target |
|---|---|
| API availability | 99.5% per month in the limited test |
| p95 API latency | < 500 ms without operations on external providers |
| Duplicate credits on replay | 0 when `idempotency_key` is repeated |
| Duplicate debits on usage replay | 0 |
| Leakage of private keys into logs | 0 |
| Delivery of aggregated usage | 95% of records not older than 120 sec on a healthy network |
| Node failure detection | < 2 min with working monitoring |
| Switch to a working node | target UX < 2 min; depends on the network and route health |
| RPO / RTO control-plane | ≤24 hours / ≤2 hours in the test environment (to be refined after a drill) |
| Automated tests | all critical paths of balance, payments, authorization, and key issuance |

Testing of throughput, battery consumption, and reconnection speed must be done on real Android devices; results are recorded by OS version and network.

## 12. Experimental compatibility with restricted networks

- Separate **DPI filtering** (protocol recognition), **IP/port filtering**, and a **strict allowlist** (access to specified endpoints).
- Obfuscation may improve resistance to DPI, **but does not create access to an arbitrary IP prohibited by an allowlist**.
- Implement the experimental mode as a set of QA-certified transports. Assign each supported versions and real observations by network type.
- Introduce a `capability matrix`, connection metrics, and controlled field tests in networks to which authorized access exists.
- Do not build functionality on spoofing other people's domains, unauthorized use of allowlisted services, or a promise of guaranteed bypass.
- If there is no technically reliable route, show an honest `unavailable` indication and offline support/diagnostics functionality.

## 13. Monetization and anti-fraud

### Base business model
- Free: a limited base quota + daily credit/clicker/verified tasks.
- Premium: payment for access with a published fair-use policy and a device limit; use the word "unlimited" only with a transparent description of the restrictions.
- Partner tasks: payment for confirmed events, not for any link click-throughs. Terms, ad labeling, and platform rules must be agreed in advance.

### Model formulas
```text
Gross margin = SubscriptionRevenue + ValidatedSponsorRevenue
               - Hosting - PaidTraffic - PaymentFees - Support - Refunds
Contribution per free user = SponsorRevenuePerFreeUser
                             - TrafficCostPerFreeUser - VariableSupportCost
AllowedRewardBytes = min(DailyCap, AvailableCampaignBudget / RealCostPerByte)
```

Do not tie the cost to a fixed "price per GB" without accounting for the VPS's included traffic, port speed, the 95th percentile, overage, and fraud. The clicker configuration must be accepted after pilot measurements of the actual traffic cost.

### KPI
- Day-1/Day-7/Day-30 retention, DAU/MAU, trial→paid conversion, ARPPU, CAC, churn.
- Average and p95 traffic consumption for Free/Premium, cost per active user, share of suspicious reward_claims.
- Connection success rate by app version, node, network type, and engine (without storing the user's sites).

## 14. Legal and publication control points

**Before public launch:**
1. Verify the permissibility of providing a VPN and advertising in the countries of the operator, hosting, distribution, and the payment provider; separately, the rules on personal data, sanctions, and business registration requirements.
2. The age of the owner: conditions for concluding contracts, developer registration, and processing; where necessary, lawful participation of an adult, not a fictitious nominal owner.
3. Google Play: the `VpnService` policy, explicit disclosure of the purpose and data, system permission; do not redirect third-party apps' advertising traffic for revenue.
4. Apple: special App Review rules apply to VPNs (including an organizational developer account, data handling, and local licenses) and the corresponding Network Extension mechanisms; a separate iOS gate.
5. Telegram: rules for digital services and Stars, bot access for verifying channel membership, advertising inventory terms, and correctness of tasks.
6. Third-party component licenses: SPDX/SBOM, GPL-3.0 for the full Amnezia client, MPL-2.0 for Xray, licenses of specific embeddable SDKs.
7. Privacy policy, terms of service, refunds/payments, and an incident notification procedure.

## 15. Test plan and acceptance criteria

### Critical backend
- Two identical `reward_claim` operations with the same key produce **exactly one** credit.
- Parallel claim requests do not exceed the daily limit.
- A re-received usage sample does not consume quota again.
- A counter reset on the node does not create a negative debit/credit.
- User A cannot obtain user B's configuration.
- A revoked device loses the right to new configurations; the revocation is applied by the node.
- A failed or unconfirmed payment does not enable Premium.
- An attacker without MFA does not obtain administrative access.

### Client
- Connect/disconnect actually changes the system VPN status.
- When Wi-Fi/mobile internet is turned on and off, the app does not hang and shows a correct error/connection retry.
- A missing VPN permission is explained without a false "Connected" status.
- Verify IPv4, IPv6, DNS, leaks, app background/foreground, split tunnel only if implemented.
- The app does not contain working secrets/admin API keys.

### Infra
- Database restoration from a backup is confirmed by an automated or manual drill.
- A new test node can be deployed from a reproducible playbook.
- Unavailability of one node does not corrupt users/balances; a control-plane failure does not terminate already live sessions without a separate cause.

## 16. Open decisions (ADR backlog)

- ADR-001: Login by a one-time code from the Telegram bot; pairing, trusted updates, sessions, and recovery. The primary method was chosen by the owner; the technical contract is being refined.
- ADR-002: Own client on Flutter + native SDK vs a GPL fork of an existing client.
- ADR-003: Base tunnel engine and compatible Android versions.
- ADR-004: Counter adapter, debit period, and over-consumption protection.
- ADR-005: Free quota per device vs per verified user.
- ADR-006: Design of advertising tasks: which actions we verify and which are permitted by platform terms.
- ADR-007: Legally permissible payments and publication regions.
- ADR-008: Node Agent contract: polling/mTLS and key management.
- ADR-009: Retention periods for security/abuse logs.
- ADR-010: Recovery option for network blocking/API unavailability.

## 17. Implementation with Codex: principles

- First design documentation/schemas and working tests, then infrastructure code.
- Do not give the AI full production root access; where necessary a sandbox/test VPS and manual approval of changes.
- One task = one issue/branch/PR, small reviewable changes.
- Every PR must contain: a description, a migration (if needed), unit/integration tests, local run commands, documentation updates.
- Fake APIs or a UI that passes a mock off as a working VPN are prohibited. Stubs are labeled `MOCK/DEV ONLY`.
- Work on payments, transport, key handling, and publication requires a separate security review.

## 18. Primary sources to verify during development

- AmneziaWG: https://docs.amnezia.org/documentation/amnezia-wg/
- AmneziaVPN client / GPL: https://github.com/amnezia-vpn/amnezia-client
- Amnezia third-party licenses: https://github.com/amnezia-vpn/amnezia-client/blob/dev/THIRD_PARTY_LICENSES.md
- Xray-core / MPL-2.0: https://github.com/XTLS/Xray-core
- Android VpnService: https://developer.android.com/develop/connectivity/vpn
- Google Play VPN policy: https://support.google.com/googleplay/android-developer/answer/12564964?hl=ru
- Apple App Review guidelines: https://developer.apple.com/app-store/review/guidelines/
- Apple Network Extension / Personal VPN: https://developer.apple.com/documentation/networkextension/personal-vpn
- Telegram Bot API: https://core.telegram.org/bots/api
- Telegram Stars TOS: https://telegram.org/tos/stars
