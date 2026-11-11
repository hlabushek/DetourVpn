# Consolidated merge plan — overnight work (2026-11-11)

**Status: RECOMMENDATION ONLY. No command in this file has been run.** Every command below is for the owner to run, one at a time, after reading the step. The consolidation session did not merge, commit, push, deploy, stash, reset or check out anything.

Companion file: `CONSOLIDATED_REPORT.md` (inventory and evidence).

---

## 0. Key finding that shapes the plan

Nothing needs to be merged. Every local and remote branch tip is an ancestor of `claude/overnight-integration` (tip `b12fbb2`), so a merge of any of them into overnight would change nothing. The branch already holds:

- `codex/i025-android-windows` (35 commits above `main`, 9 of them not on origin);
- `claude/redesign-devices-servers` (`ddf1320`);
- 15 new commits from overnight runs 1–4 (`91a0633` … `b12fbb2`).

So the real decisions are:

1. Which base branch the work should land on (`main` is 115 commits behind; `codex/i025-android-windows` is the owner's current line).
2. Whether to publish the 9 unpushed `codex/i025` commits (they contain owner work, including the I-030 snapshot).
3. Whether and how to review and sanitize before pushing (54 files need an IP-like-string check; see the report).
4. What to do with two stale untracked copies in other worktrees.

Placeholders used below:
- `<TARGET_BASE>` — the base branch for a PR. Default suggestion `main`; the owner decides.
- `<REVIEW_TAG>` — any tag or branch name the owner chooses for a safety backup.

---

## 1. Conflicts to resolve first

| Order | Item | Type | Resolution |
|---|---|---|---|
| 1 | `docs/beta-privacy-notice.md` in `.claude/worktrees/beta-launch-checklist-bdf7e1` (untracked) vs committed copy on overnight | two versions | Keep the **committed** version (newer, matches ADR-090 at 5 GiB). Do not copy the untracked draft in. Owner decides whether to delete the draft after a backup (step 4). |
| 2 | 54 files in `docs/evidence/` and `docs/overnight/` with IPv4-like strings | sanitization | Review before any push. Many may be version strings; confirm each. Redact anything that is a real address. |
| 3 | Owner snapshot commit `f912cf3` ("owner working tree snapshot", 102 files) and I-030 commits on `codex/i025-android-windows` | owner decision | Confirm the owner wants these published (they will be published with overnight). |
| 4 | Mixed commits (`ClientActivity.java`, `ClientApplication.java`, `config_factory.cpp/.h`, `openapi.json`, `db/models.py`, `test_migrations.py`, `AndroidManifest.xml`) | review | No conflict. Read the diff of each mixed commit before publish. Do not rewrite history to split them (see alternative B). |
| 5 | Product decisions in the reports: `FLAG_SECURE` removed (ADR-103); update signing key empty (ADR-107); panel delivery off by default (ADR-115); username stored (ADR-110) | owner decision | Confirm each before a public release. Not a merge conflict. |

No two-branch commit conflicts exist, so there is no step that needs a `git merge` conflict resolution.

---

## 2. Recommended order

### Step A — Freeze and confirm state (read-only, owner)

Why: the worktrees may be used by other sessions. Confirm nothing is writing to them before any action.

```bash
git worktree list
```

```bash
git -C .claude/worktrees/fixes-before-beta-0eeeba status --short
```

```bash
git -C .claude/worktrees/fixes-before-beta-0eeeba rev-parse --abbrev-ref HEAD
```

Expected (at inventory time): overnight worktree on `claude/overnight-integration`, clean except for the two new `docs/overnight/CONSOLIDATED_*.md` files written by this session (untracked).

**NOT YET RUN.**

### Step B — Confirm the overnight branch contains everything (read-only)

Why: re-verify the "all branches are ancestors" finding on the owner's machine.

```bash
git merge-base --is-ancestor codex/i025-android-windows claude/overnight-integration && echo "i025 is ancestor"
```

```bash
git merge-base --is-ancestor claude/redesign-devices-servers claude/overnight-integration && echo "redesign is ancestor"
```

```bash
git rev-list --count main..claude/overnight-integration
```

Expected: both "is ancestor" lines print; the last command prints `115`.

**NOT YET RUN.**

### Step C — Back up the refs (optional, owner decides)

Why: a cheap safety marker before any publish. Creates a local branch only; it changes no files.

```bash
git branch <REVIEW_TAG>-overnight claude/overnight-integration
```

```bash
git branch <REVIEW_TAG>-i025 codex/i025-android-windows
```

**NOT YET RUN.** Replace `<REVIEW_TAG>` with a name the owner picks, for example `pre-publish-2026-11-11`.

### Step D — Review the stale untracked draft (owner decides, read-only first)

Why: the draft in `beta-launch-checklist-bdf7e1` disagrees with ADR-090 and with the committed notice. Keep the committed version; decide what to do with the draft.

Read-only check of the draft's status:

```bash
git -C .claude/worktrees/beta-launch-checklist-bdf7e1 status --short
```

Expected: `?? docs/beta-privacy-notice.md`.

If the owner wants to keep a copy of the draft outside git first (copy only, nothing deleted):

```bash
cp .claude/worktrees/beta-launch-checklist-bdf7e1/docs/beta-privacy-notice.md <path-outside-repo>/beta-privacy-notice.draft-2026-11-08.md
```

Deleting the untracked draft is optional and separate. Dry run first:

```bash
git -C .claude/worktrees/beta-launch-checklist-bdf7e1 clean -n -- docs/beta-privacy-notice.md
```

Only after the owner confirms the copy exists and wants the draft gone:

```bash
git -C .claude/worktrees/beta-launch-checklist-bdf7e1 clean -f -- docs/beta-privacy-notice.md
```

**NOT YET RUN.**

### Step E — Duplicate untracked files in the charming worktree (optional)

Why: the six files there are byte-identical to committed copies on overnight. Removing them is cosmetic.

Dry run:

```bash
git -C .claude/worktrees/charming-grothendieck-f3d08c clean -n
```

Only after the owner confirms (the six files are identical to overnight's copies):

```bash
git -C .claude/worktrees/charming-grothendieck-f3d08c clean -f
```

**NOT YET RUN.** Check the dry-run list shows only the six I-030 files before running the second command.

### Step F — Sanitization review before any push (owner, required)

Why: 54 files match an IPv4-shaped pattern in `docs/evidence/` and `docs/overnight/`. The repo rules forbid IPs, identifiers and credentials in evidence.

List the matching files (count-only was done; this lists names, not contents):

```bash
git grep -l -E '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' claude/overnight-integration -- docs/evidence docs/overnight
```

For each hit, open it locally and confirm there is no real address, host, key or token. Redact or remove anything real in a **new** commit on the owner's side, not by rewriting history.

Also run the owner's own secret scanner if one is configured (the repo has no scanner command recorded in the reports; not verified).

**NOT YET RUN.**

### Step G — Publish the owner's unpushed base commits (owner decision)

Why: `codex/i025-android-windows` has 9 local commits not on origin, and overnight includes them. Publish them only if the owner wants the I-030 snapshot and receipts on origin.

```bash
git push origin codex/i025-android-windows
```

Note: this is a fast-forward push (origin is an ancestor), but it still publishes owner work. Skip this step if the owner wants everything to go up only with the overnight branch.

**NOT YET RUN.**

### Step H — Push the overnight integration branch

Why: nothing from overnight is on origin. This is the publish step.

Prerequisite: Steps F and G done, and the owner has read the diff (`git log --oneline main..claude/overnight-integration` and `git diff --stat main...claude/overnight-integration`).

```bash
git push -u origin claude/overnight-integration
```

**NOT YET RUN.**

### Step I — Open a PR to the chosen base (owner)

Why: review before anything reaches the base branch. The PR will be large (about 1,263 files, about 171k inserted lines vs `main`).

Replace `<TARGET_BASE>` with the chosen base (suggested `main`; `codex/i025-android-windows` is an alternative, but it is already an ancestor of overnight).

```bash
gh pr create --base <TARGET_BASE> --head claude/overnight-integration --title "Overnight integration: runs 1-4" --body-file docs/overnight/FINAL_REPORT.md
```

**NOT YET RUN.** Read the body file before creating the PR; it is the run-4 report, not a PR description. Adjust the body if the owner prefers.

### Step J — Run the checks on a clean checkout before merging (owner)

Why: the reports record test results, but none were re-run by this session. The owner should run the relevant checks on the branch tip, on a clean checkout, before merging into the base. The commands below are the owner's to choose; this session did not run them.

Examples from the run-4 report (run them only if the owner wants to re-verify):

```bash
powershell -File scripts/build_flutter_client.ps1
```

```bash
python scripts/test_with_postgres.py
```

Record the actual results in the PR. Do not treat the numbers in the reports as current until they are re-run.

**NOT YET RUN.**

### Step K — Merge (owner, after review)

Why: only after the PR is reviewed and the checks in Step J are done. Choose the merge method the owner prefers. This is not a conflict-resolution step, because there are no conflicts.

```bash
gh pr merge <PR_NUMBER> --merge
```

**NOT YET RUN.** Do not enable auto-merge; the owner merges manually.

### Step L — Device and release steps (not merge steps)

These are separate from the merge and are listed in `CONSOLIDATED_REPORT.md` section 9 (device tests). Before any beta or release: device tests on Android and Windows, a real update-signing key (keygen script, public key pasted in two places), a version bump from `0.1.0-beta.26`, and the owner's own signing variables.

**NOT YET RUN.**

---

## 3. Alternative B — split by area (not recommended)

Idea: rebuild separate branches for API, Android, Windows and admin from the ancestor chain, using cherry-picks.

Why not recommended:
- The reports say several commits mix areas, and some intermediate commits may not build on their own.
- Splitting mixed files needs interactive staging, which was not available in run 4.
- A split would create many new branches that all contain the same owner snapshot commits.

If the owner still wants it, the owner should pick the commits by hash from `git log --oneline main..claude/overnight-integration` and test each one separately. This session did not do it.

---

## 4. Branch cleanup (later, optional, owner decides)

Only after the overnight branch is merged and verified:

- Local branches that are ancestors of the base can be deleted by the owner (`git branch -d <name>`): `claude/beta-definition`, `claude/redesign-devices-servers`, `codex/i-019-node-failover`, `codex/i024-peer-windows`, `codex/publish-2026-10-23`, `claude/fixes-before-beta-0eeeba`, `claude/beta-launch-checklist-bdf7e1`, `claude/charming-grothendieck-f3d08c`. Check each with `git merge-base --is-ancestor <name> <TARGET_BASE>` first.
- Worktrees: remove only after their untracked files are resolved (Steps D and E). Do not use `git worktree remove --force` without checking the list first.

**NOT YET RUN.** None of this is needed for the merge itself.

---

## 5. Risks to keep in view during the merge

- Signed updates are inactive until the owner adds a public key (ADR-107).
- `FLAG_SECURE` removed (ADR-103): screenshots allowed on Android.
- Panel VLESS is off by default; Android has no VLESS engine (libbox not bundled).
- Telegram username is stored (ADR-110): confirm the privacy wording before release.
- Build outputs in `apps/client/build/` are unsigned or debug artifacts and must not be published as releases.
- Machine-local ignored files (`local.properties`, `infra/ansible/inventory.yml`, `infra/beta/nodes.json`, `.claude/`) must never be committed.

---

## 6. What was not done by this plan

- No merge, commit, push, deploy, stash, reset, checkout or clean was run by the consolidation session.
- No test was re-run. Recorded results are the reports' claims; see the report, section 5.
- No device was tested.
- Server state was not checked.
