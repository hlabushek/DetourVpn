# ADR-090 — definition of the closed beta

Status: proposed 2026-11-08; quota, duration and rewards values agreed by the owner in chat
2026-11-08 ("for now"). The budget figure is still open. Not yet accepted as a whole.
Documentation only; no code, no production action, no external side effect.

## Context

Beta.22 has repeatable owner evidence on one Pixel 8 Pro, one carrier and one home network
(ADR-088/089, `../prepared/I-030-append-20261108.md`). That is not launch-ready evidence.
The repository has no definition of what "closed beta" means, so launch criteria were implicit.
This ADR fixes scope, promises, stop criteria and go/no-go. Values marked **OWNER** are not
decided here; they need the owner's explicit choice and must not be filled in by assumption.
Only the VPS budget remains marked this way.

## Decision

### Scope
- 5–20 testers, added manually by the owner; no public sign-up, no public link.
- Android only. Signed beta APK distributed by a private link; the SHA-256 is sent with it.
- One to two staging/beta nodes. Support is handled personally by the owner.
- Pilot first: 2–3 testers for 3–7 days, then expand to 5–20 only if the stop criteria below
  stayed clear.
- Beta duration: 4 weeks with a review at week 2; extension needs a new ADR
  (owner agreed 2026-11-08, "for now").

### Disabled during beta
Payments (I-020), public referral campaigns (I-018 stays no-go), partner tasks, iOS,
Windows/Linux clients, second transport, advertising. Rewards (daily/clicker) are off at
start; they may be enabled after the first week only if the owner explicitly decides so and
measured consumption is within budget.

### What we promise
- A working tunnel on the tested Android versions and networks listed in the compatibility matrix.
- Quota accounted in integer bytes and visible in the app.
- Account deletion and device revocation on request.

### What we do not promise
- Bypass of strict IP allowlists or any universal circumvention (ROADMAP §11).
- Absence of all DNS/IPv6 leaks; only the limited measured samples are claimed.
- Availability, SLA, seamless failover, or protection from DDoS.
- Any compatibility outside the matrix.

### Quota and cost
- Per-tester starting quota: 512 MiB (`VPN_INITIAL_QUOTA_BYTES=536870912`), the same for the
  pilot. Chosen as a working scale: the owner test account was granted about 550 MB and a
  100–200 s session consumed 6–7 MB. Real tester consumption is not yet measured.
  The code default stays 0; the value is set only in the beta environment config.
- Ceiling for the whole beta: 20 x 512 MiB = 10 GiB granted.
- Budget rule: total granted quota must not exceed 50% of the beta node's included monthly
  traffic. **OWNER, open:** the VPS plan and its traffic limit are not recorded. If the limit is
  below 20 GiB per month, the per-tester quota drops to 256 MiB. Record the figures in the
  evidence file before launch.

### Stop criteria (any one pauses the beta and issues a revoke of new leases)
1. A secret, private key, token or user traffic data appears in a log, report or screenshot.
2. `reserved_bytes` stays non-zero or `revoke_gate` stays open after a connection is closed,
   beyond one recovery cycle, on any account.
3. Quota ledger and reconciliation disagree.
4. A node fails and does not recover through the documented runbook.
5. A tester reports a privacy complaint or an unconsented data collection.
6. Spend reaches the budget limit.

### Go / no-go before the first tester
All must be true and recorded in `docs/evidence/`. The list is taken from the open gaps in
`docs/beta-readiness-20261029.md` and its 2026-11-02 follow-up on branch
`codex/i025-android-windows` (the branch this ADR's worktree was cut from, `main`, predates it;
items already accepted there, such as admin MFA containment, off-host backup scheduling, the
I-019 drill and the two-device check, are not repeated here):
- I-026 friend onboarding (invite, OTP login) verified beyond the owner-only flow;
- natural scheduled backup trigger observed, and a database restore from the imported archive;
- signing keystore restore and password recovery verified; physical update install over the
  same signed package; private APK distribution verified on a non-owner device;
- a designated support recipient and tested tester instructions (the diagnostic share flow has
  no recipient yet);
- phase-6 operating evidence: usage-lag, memory/battery baselines, incident runbook and node
  rotation, free-traffic cost measured, basic SLOs;
- account deletion implemented, migrated and tested (not found in `services/api`; the only
  match is a docstring saying the backup helper does no account deletion);
- no open high/critical findings from the internal security review;
- `reserved_bytes=0` and `revoke_gate=closed` after every drill, and the hung first LTE
  connection from ADR-089 either explained or covered by a user-visible retry.

Not promised, and not a gate: broad packet/DNS/IPv6 audit (deferred by ADR-072), Always-on and
kill switch (disabled in the release manifest), automatic failover.

An external security review is deferred until the audience grows beyond 20 testers.

## Consequences and limits

- The beta is evidence-driven, not date-driven: a missing item moves the launch.
- Open items stay open: cause of the hung first LTE connection (ADR-089), independent egress
  measurement, live Telegram login check.
- This ADR does not approve any VPS purchase, DNS/account registration or production deploy;
  each needs separate explicit approval.
