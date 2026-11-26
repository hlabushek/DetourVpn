# Git workflow: worktrees, branches, builds

Status: working rules, 2026-11-24. The repository has one history, shared by every folder.

## How the pieces fit

- **Branch** = a named line of commits. Only commits are shared between folders.
- **Worktree** = a folder with one branch checked out. Each Claude session gets its own worktree and branch, usually under `.claude/worktrees/`.
- **Commit** = saved work. Only committed work is in a branch and can be built reliably.
- **Uncommitted / untracked files** exist only in their own folder. A new session cannot see them.
- **Push** = copy of a branch to `origin` (GitHub). Only pushed commits survive loss of the machine.
- **Build** (APK, Windows zip, API deploy) uses the folder's content at build time, including uncommitted changes. A build is therefore identified by its commit hash, not by a file name or session name.

## Before a build or deploy

Run the read-only report from the repository root:

```powershell
python scripts/git_state.py
```

Build from a clean folder on a committed, pushed commit. The report must show no `UNSAVED` line for that folder. Record `git rev-parse --short HEAD` in the build receipt.

## Starting a new session

1. Check the report first. Do not assume a previous session's changes are present or current.
2. Start from the branch that holds the latest work (today: `claude/connection-reliability`), or from `main` only for work that must not include unmerged changes.
3. One branch per task. Do not start a second session in a folder where another session is still editing.

## Ending a session

1. Commit the work you want to keep (`git add` specific files, then `git commit`).
2. Push the branch if the work must survive: `git push -u origin <branch>`.
3. Run `python scripts/git_state.py` and confirm there is no `UNSAVED` or `NOT PUSHED` line for the branch you used.

## Current state (snapshot, 2026-11-27, from `git log` and `scripts/git_state.py`; the owner updates it when the line changes)

- **Current line of work:** `claude/connection-reliability`. On origin: `d6f9cb6` (beta.37 release console). Locally ahead by the 2026-11-27 overnight commits (`f27bfe3` .. `ecd15e4`, plus the commit that updates this section); **not pushed**. Start new work from here.
- **`main`:** at `ae27e37` (project instructions for versions, worktrees and builds). It is an ancestor of `claude/connection-reliability`, which is 12 commits ahead of it. Merging further into `main` is an owner decision.
- **`codex/i025-android-windows`:** the main folder (`9b0bbc0`). It holds unsaved Android reliability changes (15 changed, 15 untracked) and 10 commits not on origin. Do not commit, push, or reset it without the owner's go-ahead.
- **Rescue branch:** `backup/android-reliability-snapshot-2026-11-12` (commit `4538bb0`). It keeps the 12 November Android snapshot. Not pushed. `codex/i025-android-windows` is fully inside it.
- **Other local-only branches:** `candidate/api-3600-plus-audit` (`c0d134e`, checked out in a session scratchpad worktree, 12 commits, no remote), `candidate/api-3600-min` (fully inside the previous one), `wip/i025-android-reliability-20261124` (2 commits, no remote). None is pushed.
- **Removed on 2026-11-24:** duplicate and empty worktrees and branches, and build caches. Copies are in `C:\Users\JetGame\DetourVpn-cleanup-backup-2026-11-24\`.

If `scripts/git_state.py` disagrees with this section, trust the script and update this section.

## Rules this repository follows

- Never `git reset`, `git clean`, `git stash` (without a unique tag) or delete a branch or folder without the owner's explicit instruction.
- Merging into `main` is a separate, owner-approved step.
- Branches reported as `fully inside <branch>` hold no unique work. Deleting them is still an owner decision.
- `scripts/git_state.py` only reads. It runs `git fetch` only with `--fetch`.
