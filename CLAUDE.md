# Claude Code instructions

@AGENTS.md

## Safe work in this repository

- Treat this as a security-sensitive VPN and real-infrastructure project. Follow `AGENTS.md`, `TECHNICAL_SPEC.md`, and `ROADMAP.md` where relevant.
- Before acting, inspect `git status --short`, the assigned issue, its current ADRs, runbooks, and evidence. The working tree may contain important uncommitted and untracked work.
- Never discard, reset, clean, stash, stage, commit, or overwrite existing work unless the owner explicitly asks. Do not run two coding agents against this working tree at the same time.
- Stay within the one issue explicitly assigned by the owner. For I-030, do not initiate a physical VPN Connect, alter beta state, quota, or reservations unless the current issue decision explicitly authorizes that exact action.
- Treat staging/beta infrastructure access as read-only unless a specific operation has explicit authorization. Never use production credentials or request secrets in chat; the owner enters credentials only in local secure prompts.
- Do not expose identifiers, credentials, IP addresses, endpoints, raw logs, traffic, or account details in evidence or responses. Save only the minimum sanitized evidence needed for the assigned issue.
- Make the smallest reviewable change. Before editing, state the files to change, relevant checks, and security risks. Run only checks needed for the change or acceptance criteria; do not launch a broad test suite by default.
- Do not treat a build, mock, local VPN state, UI message, ping, or quota debit as proof of tunnel handshake, source-bound egress, server attribution, or beta readiness.
- Explain results and tradeoffs in English. Distinguish verified facts, owner reports, and unresolved questions.

## Versions, worktrees and builds

Full rules: `docs/git-workflow.md`. Short version:

- Each session works in its own folder (worktree) on its own branch. Commits are shared by all folders; uncommitted and untracked files exist only in their own folder.
- At the start of every session, run `python scripts/git_state.py` and trust its output over memory notes or older documents. It shows which folder holds which branch, what is unsaved, and what is not pushed.
- The current line of work is the branch named in `docs/git-workflow.md` ("Current state"). Start new work from that branch, not from `main`, unless the owner says otherwise. If the script shows a branch with newer commits than that section, ask the owner before continuing.
- Build only from a committed commit. `scripts/release_beta.ps1` refuses a dirty tree; other build scripts do not check, so check `git status` yourself and record the commit hash (`git rev-parse --short HEAD`) in the build receipt.
- Never delete a branch, worktree, or untracked file without the owner's explicit approval. Before any removal, copy the item outside the repository and say how to restore it.
- At the end of a session: commit the files you own, push the branch if it must survive, and run `python scripts/git_state.py` once more.
