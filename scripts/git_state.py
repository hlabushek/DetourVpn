#!/usr/bin/env python3
"""Read-only report of git worktrees, branches and unsaved work.

Shows, for every worktree folder, which branch is checked out and whether it
has uncommitted or untracked files. For every local branch, shows how far it
is from main, whether its commits are pushed, and whether it is already
contained in another branch.

It never changes anything: no commit, stash, reset, checkout, delete or push.
With --fetch it only updates remote-tracking refs (git fetch --prune).

Usage (from any folder of the repository):
    python scripts/git_state.py
    python scripts/git_state.py --fetch
"""

import argparse
import subprocess
import sys


def git(args, cwd=None):
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def parse_worktrees(text):
    """Parse `git worktree list --porcelain` into a list of dicts."""
    items, current = [], {}
    for line in text.splitlines():
        if not line:
            if current:
                items.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        items.append(current)
    return items


def ahead_behind(base, branch):
    """Return (ahead, behind) of `branch` relative to `base`."""
    out = git(["rev-list", "--left-right", "--count", f"{base}...{branch}"]).split()
    behind, ahead = int(out[0]), int(out[1])
    return ahead, behind


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--main", default="main", help="integration branch (default: main)")
    parser.add_argument("--fetch", action="store_true", help="run git fetch --prune first (network)")
    args = parser.parse_args()

    if args.fetch:
        git(["fetch", "--prune"])
        print("Fetched remotes (remote-tracking refs updated).\n")

    problems = []

    # 1. Worktrees: where the working files live.
    print("== Worktrees (folders where work can be unsaved)")
    worktrees = parse_worktrees(git(["worktree", "list", "--porcelain"]))
    checked_out = set()
    for wt in worktrees:
        path = wt.get("worktree", "?")
        branch = wt.get("branch", "(detached)").replace("refs/heads/", "")
        checked_out.add(branch)
        status = git(["status", "--porcelain"], cwd=path).splitlines()
        untracked = sum(1 for line in status if line.startswith("??"))
        changed = len(status) - untracked
        print(f"- {path}")
        print(f"    branch: {branch}   HEAD: {wt.get('HEAD', '')[:7]}   "
              f"uncommitted: {changed}   untracked: {untracked}")
        if changed or untracked:
            problems.append(
                f"UNSAVED: {changed} changed + {untracked} untracked file(s) only in {path} (branch {branch})"
            )

    # 2. Branches: how far each is from main, pushed or not, and contained elsewhere.
    print(f"\n== Local branches compared with {args.main}")
    rows = []
    for line in git(["for-each-ref", "--format=%(refname:short)|%(upstream:short)", "refs/heads"]).splitlines():
        name, _, upstream = line.partition("|")
        ahead_main, behind_main = ahead_behind(args.main, name)
        if upstream:
            unpushed = int(git(["rev-list", "--count", f"{upstream}..{name}"]).strip())
        else:
            unpushed = ahead_main
        rows.append((name, upstream, ahead_main, behind_main, unpushed))

    # A branch is "fully inside" another if its tip is an ancestor of that other branch.
    # Only branches with commits of their own are compared; main is never a container here.
    tips = {name: git(["rev-parse", name]).strip() for name, *_ in rows}
    ahead_of = {name: ahead for name, _, ahead, _, _ in rows}
    contained_in = {}
    for name, _, ahead_main, _, _ in rows:
        if ahead_main == 0 or name == args.main:
            continue
        containers = [
            other for other in tips
            if other not in (name, args.main)
            and tips[other] != tips[name]
            and ahead_of[other] > ahead_main
            and subprocess.run(["git", "merge-base", "--is-ancestor", name, other],
                               capture_output=True).returncode == 0
        ]
        if containers:
            # Report the largest containing branch: the one that already holds the work.
            contained_in[name] = [max(containers, key=lambda o: ahead_of[o])]

    for name, upstream, ahead_main, behind_main, unpushed in rows:
        where = "checked out in a worktree" if name in checked_out else "no worktree"
        upstream_text = upstream or "NO remote branch"
        line = (f"- {name}: +{ahead_main} / -{behind_main} vs {args.main}; "
                f"unpushed: {unpushed}; remote: {upstream_text}; {where}")
        if name in contained_in:
            line += f"; fully inside {', '.join(contained_in[name])}"
        print(line)
        if unpushed and name in checked_out:
            problems.append(f"NOT PUSHED: {name} has {unpushed} commit(s) not on {upstream_text}")
        elif unpushed and ahead_main and name not in contained_in:
            problems.append(f"ONLY LOCAL: {name} has {ahead_main} commit(s) not in {args.main} and not in another branch")

    # 3. Summary.
    print("\n== Attention")
    if problems:
        for p in problems:
            print(f"! {p}")
    else:
        print("Nothing unsaved or unpushed.")
    print(f"\nMain branch: {args.main}. Branches listed as 'fully inside ...' can be deleted later, "
          f"but only after the owner agrees. This script never deletes anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
