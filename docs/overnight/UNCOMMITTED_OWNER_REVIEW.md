# Files not committed — owner review (run 4, 2026-11-11)

Branch `claude/overnight-integration` (created from `claude/redesign-devices-servers` at
`ddf1320`). Nothing was pushed.

## Result
No changed file was left uncommitted for an ownership reason. Every uncommitted change in this
worktree at the start of run 4 came from earlier Claude sessions working on your instructions:
the fault-tolerance work (ADR-096..102) and overnight runs 1–3 (ADR-103..114). Your session
notes say so (memory "fault-tolerance-work-2026-11-11"). No hunk written by you personally was
found. The two runbook additions (`beta-add-node.md`, `beta-lease-expiry.md`) cite ADR-096..101,
so they came from the same sessions and were committed.

## Left out on purpose
| Path | Why |
|---|---|
| `.claude/launch.json` | Machine-local preview config. Now in `.gitignore`. |
| Caches and build outputs (`.mypy_cache/`, `apps/client/android/local.properties`, `.cache/`, `build/`, `.dart_tool/`) | Already ignored by existing `.gitignore` patterns; nothing to add. |

## Mixed files (committed, but carry more than one area)
Hunk-level splitting needs interactive staging, which is not available unattended. So each
mixed file went into the commit of its main area. The commit message names the other areas:
- `ClientActivity.java`: robustness commit; also update channel (ADR-107), DNS (ADR-106), and
  the FLAG_SECURE removal (ADR-103).
- `ClientApplication.java`: robustness commit; also the DNS rewrite (ADR-106).
- `config_factory.cpp/.h`, `config_factory_tests.cpp`: panel/VLESS commit; also DNS (ADR-106).
- `openapi.json`, `db/models.py`, `test_migrations.py`: panel commit; also carry the contract
  and model changes of the robustness and username commits.
- `AndroidManifest.xml`: update commit; also the boot receiver (ADR-098).

Some intermediate commits may therefore not build on their own. The branch tip builds and
passes the checks listed in `FINAL_REPORT.md`.
