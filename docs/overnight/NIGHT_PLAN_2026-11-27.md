# Overnight plan, 2026-11-27 (branch claude/connection-reliability)

Owner is asleep. The agent works alone and reports in the morning. No physical device, no
server access, no deploy, no push, no merge into main. Local commits on this branch only.

## Hard limits (from CLAUDE.md, AGENTS.md, docs/git-workflow.md)

- Work only in this worktree. Do not touch `codex/i025-android-windows` (uncommitted work there).
- Never reset, clean, stash, delete a branch or delete files. Only add or edit files this plan names.
- One agent over this working tree at a time. Subagents run sequentially.
- No SSH to VPS/staging/beta. No VPN Connect. No `run_overnight_build.ps1` (it signs an APK and asks for secrets).
- No secrets, IPs, URLs, DNS names or raw logs in any file.
- Every subagent runs on the `opus` model. Each writes its result to `docs/overnight/` and
  lists every file it changed.
- Commits: one per completed step, message ends with the Co-Authored-By line. No push.

## Track A: close the small items (sequential)

| # | Step | Output | Done when |
|---|------|--------|-----------|
| A1 | Baseline tests: `flutter test`, `dart analyze` (on an ASCII copy, per memory note), Java JUnit substitute | `docs/overnight/A1_TEST_BASELINE.md` | Pass/fail counts recorded with exact commands |
| A2 | Review diff of ADR-144..154 for bugs in state, timers, restarts | `docs/overnight/A2_REVIEW_ADR144_154.md` | Findings with file:line and severity; clear bugs fixed only with a test |
| A3 | Decide `bind_refused` routing for the health probe (TunnelProbeRunner, ADR-148) | New ADR (next free number, >= 198) + small code note | ADR written; owner-decision flagged; no behaviour change without a test |
| A4 | Restart order after `restartInPlace` / `setUnderlyingNetworks` (`ClientApplication.restartTunnel`) | Unit test where possible, else device checklist entry | Test passes or checklist item exists |
| A5 | Phone checklist for phases 1-6 (what to tap, what to expect, what to record) | `docs/overnight/A5_PHONE_CHECKLIST.md` | Every phase has steps and an expected result |
| A6 | Is `scripts/run_overnight_build.ps1` still needed? Read-only analysis | `docs/overnight/A6_OVERNIGHT_BUILD_STATUS.md` | Recommendation, no run |
| A7 | Phase 3.1 read-only server check runbook (window 900 vs 3600) | `docs/overnight/A7_WINDOW_3600_CHECK.md` | Commands listed for the owner to run; agent does not execute them |
| A8 | Refresh `docs/git-workflow.md` "Current state" section | Edit to that section only | Matches `git log` and `git_state.py` output |

## Track B: two new directions (sequential, after Track A)

Chosen from the list offered to the owner: (1) automatic node choice by measured quality,
(3) owner health panel, aggregates only.

| # | Step | Output | Done when |
|---|------|--------|-----------|
| B1 | Automatic node choice: pure selection logic on the client with tests, plus ADR. No server or deploy change. | ADR + `Dart` pure module + unit tests | `flutter test` passes for the new module |
| B2 | Owner health aggregates: API read endpoint returning counts only (no IPs, DNS, URLs), with tests and OpenAPI update | Endpoint + tests + OpenAPI + ADR | pytest for the new endpoint passes on the portable PostgreSQL (`scripts/test_with_postgres.py`) |

## Morning report

The agent writes `docs/overnight/MORNING_REPORT_2026-11-27.md`: each step, result, commit hash,
what failed, what needs the owner (device tests, ADR decisions, the deploy window).
