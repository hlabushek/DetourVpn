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
