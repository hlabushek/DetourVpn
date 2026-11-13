# Uncommitted and untracked files audit — claude/connection-reliability

Date: 2026-11-13
Worktree: `.claude/worktrees/fixes-before-beta-0eeeba`
Branch checked: `claude/connection-reliability`, HEAD `34eda8e` (matches the expected starting point).
Scope: audit and tidy only. Nothing was deleted, reset, stashed, cleaned, pushed or deployed. No server or production system was touched.

## 1. Inventory (at the start of this run)

`git status --porcelain=v1 --untracked-files=all`:

| Kind | Count |
|---|---|
| Modified (unstaged) | 0 |
| Staged | 0 |
| Deleted | 0 |
| Untracked (not ignored) | 7 |

All 7 untracked paths are under `docs/evidence/` and match `i-030-beta-apk-*.json`:

| Directory | Extension | Untracked count |
|---|---|---|
| `docs/evidence/` | `.json` | 7 |

Note on the brief: the brief expected many uncommitted overnight files in this worktree. They are not present. The overnight reliability work is already committed on this branch (155 commits ahead of `main`). For example, ADR-096..102 is in `91a0633` and `33b2352`/`4e243aa`, ADR-124..127 in `be92dd6` and `4e243aa`, and ADR-133..138 in `29e912b`. All 7 `docs/adr` files for 096–102 and 15 for 124–138 are tracked. The worktree was not reassigned: the branch and HEAD match. A sibling worktree, `beta-launch-checklist-bdf7e1` (`claude/beta-launch-checklist-bdf7e1`), exists and is not touched by this run.

Ignored entries (`git status --ignored`), all matched by existing `.gitignore` rules:

| Path | Kind | Size (KiB, `du -sk`) |
|---|---|---|
| `.cache/` | cache | 8 |
| `.venv/` | Python virtualenv | 177,529 |
| `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/` | tool caches | not measured |
| `services/api/.mypy_cache/` and `__pycache__/` trees under `services/api`, `services/node-agent`, `scripts` | Python caches | not measured |
| `apps/client/.dart_tool/` | Flutter cache | 44,410 |
| `apps/client/.flutter-plugins-dependencies`, `apps/client/.metadata` | Flutter generated | not measured |
| `apps/client/build/` | Flutter build output | 982,995 |
| `apps/client/android/build/` | Gradle build output | 128 |
| `apps/client/android/.gradle/` | Gradle cache | 779 |
| `apps/client/android/local.properties` | local SDK paths | not measured |
| `apps/telegram-bot/node_modules/` | npm dependencies | 2,481 |
| `apps/telegram-mini-app/node_modules/` | npm dependencies | 79,851 |
| `apps/telegram-mini-app/dist/` | build output | 360 |
| `.claude/` | local Claude Code state, including worktrees | not measured — do not touch |
| `infra/beta/nodes.json` | **not a cache** — beta node configuration (991 bytes) | — |
| `third_party/amnezia-box/amnezia-box-v1.260910.0-src.zip` | **not a cache** — third-party source archive (1.2 MB), ignored by `*.zip` | — |

## 2. Classification

| Group | Meaning | Files | Action in this run |
|---|---|---|---|
| (a) GENERATED | caches, build outputs, SDK-generated trees, local SDK paths | all ignored entries in section 1 except the two "not a cache" rows | Already git-ignored. Nothing to change. Not deleted. |
| (b) OURS-COMMITTABLE | overnight-created, uncommitted | **0** | None found, so nothing committed. |
| (c) OWNER-REVIEW | owner-looking or mixed, uncommitted | **0 uncommitted**. Two ignored files need owner attention: `infra/beta/nodes.json` (beta node config, ignored) and `third_party/amnezia-box/…src.zip` (third-party archive, ignored) | Not touched, not committed, not deleted. |
| (d) EVIDENCE-OR-RECEIPTS | untracked evidence files | **7** (see below) | Left untracked. Not committed, not ignored. |

### Group (d) — untracked evidence (7 files)

Each file is about 594 bytes. A count-only check found no IPv4-like strings and no secret-like keywords in any of them. The content was not printed.

| File | Reason it stays untracked |
|---|---|
| `docs/evidence/i-030-beta-apk-20261111T190858931Z-64aeca07.json` | Owner or run receipt; no tracked doc refers to this exact name |
| `docs/evidence/i-030-beta-apk-20261111T202147098Z-22f605c3.json` | Same |
| `docs/evidence/i-030-beta-apk-20261112T135450229Z-38b73e31.json` | Same |
| `docs/evidence/i-030-beta-apk-verify-20261111T220900.json` | Same |
| `docs/evidence/i-030-beta-apk-verify-20261111T232147.json` | Same |
| `docs/evidence/i-030-beta-apk-verify-20261112T164725.json` | Same |
| `docs/evidence/i-030-beta-apk-verify-20261112T165450.json` | Same |

The tracked folder already holds 58 `i-030-beta-apk-*` evidence files from earlier runs, so this is the same kind of file. They are not in group (b) because they are not overnight-created code or docs, and the brief says evidence stays out of commits unless the owner decides. The owner should decide whether to commit them, since they may be sanitized receipts from the owner's own APK runs.

## 3. Commits made in this run

None. Group (b) was empty, so there was nothing to commit.

## 4. .gitignore changes

None. Every generated cache in section 1 is already ignored. A `.gitignore` change would hide nothing new, and the brief says not to change rules without need.

## 5. Checks run

- `git rev-parse --abbrev-ref HEAD` → `claude/connection-reliability`
- `git log --oneline -3` → `34eda8e`, `29e912b`, `0835dc4`
- `git status --porcelain=v1 --untracked-files=all` → 7 untracked files, nothing else
- `git diff --stat` and `git diff --cached --stat` → empty
- `git stash list` → empty
- `git check-ignore -v` on `infra/beta/nodes.json`, `*.zip` and `local.properties` → each matched by an existing rule (lines 72, 48, 61 of `.gitignore`)
- Count-only check of the 7 untracked evidence files: 0 IPv4-like strings and 0 secret-like keywords each
- `git grep` for the 7 evidence file names in tracked files (outside `docs/evidence`) → 0 references

No build, analyze or test command was run, because no code changed in this run and no commit was made.

## 6. Needs approval (owner decision)

Nothing in this run is deleted. Owner can decide the following. The commands are given for the owner to run themselves. Do not run them without a decision.

1. **Commit or keep out the 7 evidence files (group d).** Options: keep untracked, or commit as sanitized receipts after the owner checks them. No command needed to keep them as they are.
2. **Free disk space (optional, group a only, all regenerable):**
   ```bash
   rm -rf apps/client/build
   ```
   (about 960 MB; Flutter rebuilds it.)
   ```bash
   rm -rf apps/client/android/build apps/client/android/.gradle
   ```
   ```bash
   rm -rf apps/client/.dart_tool
   ```
   Keep `.venv`, `node_modules` and `.claude/` unless the owner wants them rebuilt.
3. **Ignored owner files — keep unless the owner says otherwise:** `infra/beta/nodes.json` and `third_party/amnezia-box/amnezia-box-v1.260910.0-src.zip`. Both are needed for beta and build paths. Do not delete.
4. **Root `.cache/` (8 KiB).** Not identified as generated by any tracked script in this run. Left as-is; owner should confirm what writes it before any removal.

## 7. What is left untouched

- All 7 untracked evidence files (group d).
- All ignored entries in section 1, including `.venv`, `.claude/`, `node_modules` and build folders.
- This audit file itself. It is a new untracked file, left untracked on purpose: the brief limits commits to group (b), and the owner should read it before anything is committed.
