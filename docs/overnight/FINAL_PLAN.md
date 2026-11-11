# Final overnight plan — 2026-11-11 (run 4)

Goal: close every software task that does not need a physical device. Nothing is pushed,
deployed, or run against a server. Physical device tests stay with the owner.

## Starting state (verified)
- Branch `claude/redesign-devices-servers`, 143 changed/untracked paths, all from earlier
  Claude sessions run on the owner's instructions (fault tolerance ADR-096..102, runs 1-3
  ADR-103..114). No separate owner-authored hunks were identified.
- Caches (`.mypy_cache/`, `local.properties`, `.cache/`, build dirs) are already ignored by
  existing `.gitignore` patterns. Only `.claude/launch.json` is a new machine-local file.
- Pinned toolchains exist locally: Go 1.25.5 + module cache, gomobile (SagerNet), Temurin 17,
  NDK 27/28, Flutter 3.47.7, JDK 21, MSVC 2022, portable PostgreSQL.
- The pinned engine source requires `github.com/metacubex/utls v1.8.3`, which is in the local
  module cache, so a `with_utls` build can be tried offline (`GOPROXY=off`).

## Order, dependencies, risks

| Step | Task | Depends on | Main risk | Mitigation |
|---|---|---|---|---|
| 1 | T1 commits on `claude/overnight-integration` | — | Mixing areas in shared files | Explicit paths only; shared files go into the commit of their main area, listed in the report |
| 2 | T2 full API suite | T1 (stable tree) | Postgres tests need a throwaway cluster | `scripts/test_with_postgres.py`, local only |
| 3 | T3 engine `with_utls` (Windows) | — | Changes the engine AWG testers use | Tag only adds code paths; AWG config unchanged; record new SHA-256; ADR |
| 4 | T4 panel servers to devices | T3 for "connectable on Windows" | New API field; leaking panel secrets | Device list gets only what the client needs to connect; admin-only fields stay admin-only; tests |
| 5 | T5 Android VLESS gate | ADR-114 | Turning on an untested engine | Gate stays off by default; AAR only from pinned inputs |
| 6 | T6 signed update manifest | — | Key handling | Public key in repo, private key only via an argument path; tests valid/tampered/wrong key |
| 7 | T7 subscription parsers | Official docs | Unverified formats | Implement only verified formats; synthetic samples; ADR for the rest |
| 8 | T8 handshake memory | RCA §9 | Behaviour change in renewal | Only if low risk + unit tests; no new stop code without a native signal |
| 9 | T9 checks | all code | — | Report only results actually seen |
| 10 | T10 builds | T9 | Environment failures | Record exact error |
| 11 | T11 docs + FINAL_REPORT.md | all | — | — |

Each task after T1 is committed separately on the same branch once its checks pass.

## Decisions taken up front
- Commit trailer: the task text named Haiku 5.5; this run is Opus 5.5, so commits carry the
  accurate `Co-Authored-By: Claude Opus 5.5` line (recorded in the report).
- `.claude/launch.json` is machine-local: added to `.gitignore`, not committed.
