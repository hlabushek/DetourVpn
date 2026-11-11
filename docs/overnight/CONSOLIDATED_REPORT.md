# Consolidated report — overnight work inventory (2026-11-11)

Written by a read-only consolidation session. **Nothing was merged, committed, pushed, deployed, stashed, reset or checked out.** No server, VPS or panel was contacted. The only files written are this report and `CONSOLIDATED_MERGE_PLAN.md`, both in `docs/overnight/`. Inventory time: 2026-11-11 ~15:10 (+03:00). "Last 17 hours" = since about 2026-11-10 22:10 (+03:00).

Scope note: the session that started this one ran in worktree `.claude/worktrees/fixes-before-beta-0eeeba`. At start, that worktree was on `claude/overnight-integration` (matches the start-of-conversation git snapshot), so it was not recycled to another branch during this session.

---

## 1. Bottom line (plain English)

- **4 worktrees, 11 local branches.** Remote branches on `origin`: `main`, `codex/i025-android-windows`, `codex/publish-2026-10-23`. **No overnight branch exists on origin.** Nothing from overnight has been pushed.
- **`claude/overnight-integration` already contains everything.** Its tip `b12fbb2` has every other local and remote branch tip as an ancestor. Measured with `git rev-list --count <other>..claude/overnight-integration` and `<overnight>..<other>`: for every other branch the "only on other branch" count is **0**. There is no commit on any other branch that is missing from overnight.
- **Overnight history is one line:** `main` (`c001d3d`) → `codex/i025-android-windows` (35 commits, tip `b3bbe9d`) → `claude/redesign-devices-servers` (`ddf1320`) → 15 new commits from today's overnight runs 1–4 (`91a0633` … `b12fbb2`). Overnight is **115 commits above `main`** and **80 above `codex/i025`**.
- **So there are no real branch conflicts.** There are no two diverging lines of commits to reconcile. The only real divergence is one uncommitted document (section 4).
- **Worktrees are almost all clean.** The overnight worktree and the main checkout have no uncommitted changes. Two other worktrees have untracked files only; one of those files is stale.
- **Most important decision for the owner:** whether and how to publish `claude/overnight-integration` (about 1,263 files and ~171k inserted lines vs `main`), and which base branch to target. Details and commands are in `CONSOLIDATED_MERGE_PLAN.md`.

---

## 2. Worktrees and branches

| Worktree path (short) | Branch | Tip | Uncommitted | Notes |
|---|---|---|---|---|
| main checkout (`DetourVpn`) | `codex/i025-android-windows` | `b3bbe9d` | none | 9 commits ahead of `origin/codex/i025-android-windows` (not pushed). Has ignored machine-local files (section 7). |
| `.claude/worktrees/beta-launch-checklist-bdf7e1` | `claude/beta-launch-checklist-bdf7e1` | `c001d3d` (= main) | 1 untracked: `docs/beta-privacy-notice.md` | Branch has no own commits. Untracked file is an **older draft** that differs from the committed version (section 4). |
| `.claude/worktrees/charming-grothendieck-f3d08c` | `claude/charming-grothendieck-f3d08c` | `c001d3d` (= main) | 6 untracked (I-030 ADRs 086–088, one evidence JSON, two scripts) | All 6 files are **byte-identical** to the copies already committed on `claude/overnight-integration`. Nothing unique. |
| `.claude/worktrees/fixes-before-beta-0eeeba` | `claude/overnight-integration` | `b12fbb2` | none | This session's worktree. Path name says "fixes-before-beta" but the branch is overnight. The branch `claude/fixes-before-beta-0eeeba` (at `c001d3d`) is not checked out anywhere. |

Other local branches (no worktree):

| Branch | Tip | Relation to overnight |
|---|---|---|
| `claude/beta-definition` | `15584d3` | ancestor of overnight (0 unique commits) |
| `claude/redesign-devices-servers` | `ddf1320` | ancestor of overnight (0 unique); overnight adds 15 on top |
| `codex/i-019-node-failover` | `c001d3d` | ancestor (0 unique) |
| `codex/i024-peer-windows` | `f4ef11d` | ancestor (0 unique); also `codex/publish-2026-10-23` at the same tip |
| `codex/publish-2026-10-23` | `f4ef11d` | ancestor (0 unique) |
| `main` | `c001d3d` | ancestor (0 unique); tracks `origin/main`, far behind |

Remote branches (`origin/*`): all ancestors of overnight (0 commits ahead).

---

## 3. Commits in the last 17 hours

Only `claude/overnight-integration` has commits in the window (plus `ddf1320`, which is also the tip of `claude/redesign-devices-servers`). All other branches last moved on 2026-10-22 … 2026-11-08.

| Hash | Time (+03) | Subject |
|---|---|---|
| `ddf1320` | 11-10 23:26 | chore(windows): 0022 beta deploy script and gate helper mode (I-032) — shared with `claude/redesign-devices-servers` |
| `91a0633` | 11-11 14:01 | feat: connection robustness, pool nodes, latency and handshake diagnostics (ADR-096..102) |
| `8a10e3d` | 14:01 | feat(auth): store the Telegram username from Mini App login (ADR-110) |
| `dbfce44` | 14:01 | feat: panel server import, admin user list and Windows VLESS config (ADR-105, 111..114) |
| `25ac047` | 14:01 | feat: in-app update for Android (ADR-107) |
| `0d7db5d` | 14:01 | feat: user DNS choice on Android and Windows (ADR-106) |
| `952a090` | 14:01 | feat(client): orbit as second tap target; allow screenshots (ADR-103, ADR-104) |
| `a5a1b93` | 14:01 | docs: ADRs 096-114, overnight reports and runbooks |
| `c7366d2` | 14:05 | build(windows): engine with with_utls for VLESS + Reality; config self-test (ADR-112) |
| `c09ff26` | 14:18 | feat: deliver panel servers to devices; VLESS connect on Windows (ADR-115) |
| `75e4ef9` | 14:27 | feat(android): with_utls libbox AAR build and libbox VLESS probe behind the gate (ADR-114) |
| `8193787` | 14:27 | feat(api): parse verified 3x-ui/Remnawave subscription bodies (ADR-116) |
| `851c56e` | 14:34 | feat: Ed25519-signed in-app update manifest (ADR-107) |
| `55a5e74` | 14:39 | feat: native handshake-seen memory for diagnostics on Android and Windows (task 7) |
| `4694e68` | 14:54 | docs: final run 4 report, runbooks, test filler fix |
| `b12fbb2` | 14:54 | docs: record final commit hash in the run 4 report |

Note: commit timestamps for runs 1–4 cluster at 14:01–14:54, which means they were committed in one batch at the end of run 4 (the reports say so), not as the work happened.

---

## 4. Uncommitted work, grouped by area

**Overnight worktree and main checkout:** nothing uncommitted (`git status --short` empty).

**`beta-launch-checklist-bdf7e1` (1 file, docs):**
- `docs/beta-privacy-notice.md` (7,561 bytes, mtime 2026-11-08 16:07). It **differs** from the committed version on `claude/overnight-integration` (last touched in `66c5038`). The uncommitted draft still says the quota is 5 GiB but flags it as a mismatch with ADR-090 "512 MiB, ceiling 10 GiB". The committed version says 5 GiB per owner decision 2026-11-08 and matches the committed ADR-090 (5 GiB, ceiling 20 × 5 GiB). **The committed version is newer; the untracked draft is stale.** It also has different wording on logs and backups. This is the only true two-version conflict found.

**`charming-grothendieck-f3d08c` (6 files, I-030 area, owner work):**
- `docs/adr/086-i030-control-client-tunnel-diagnosis.md`, `087-i030-phone-lte-single-attempt.md`, `088-i030-beta22-repeat-series.md`
- `docs/evidence/i-030-beta22-lte-success-20261108.json`
- `scripts/i030_control_client.py`, `scripts/i030_node_readonly_monitor.py`
- All six are identical (SHA-1 prefix compared) to the copies committed on overnight. They are duplicates, not unique work.

**Areas present in committed overnight work (for the owner's review; all committed):**
- API (`services/api`): ADR-096..102 robustness, pool nodes, latency; ADR-105 admin Telegram ID; ADR-107 signed manifest; ADR-110 username (migration `0026_telegram_username`); panel sources (migration `0027_panel_server_sources`); panel delivery (ADR-115); subscription parser (ADR-116).
- Android client (`apps/client/android`, `apps/client/lib`): update installer and provider; DNS choice; orbit tap; handshake memory; panel-server text; `FLAG_SECURE` removed (ADR-103).
- Windows client (`apps/client/windows`): VLESS/Reality config generator; `with_utls` engine build; DNS choice in `config_factory`; `--vless-selftest`.
- Admin UI (`apps/admin`): Telegram ID column, panel-server list and import form.
- Scripts and infra: `scripts/app_update_keygen.py`, `scripts/prepare_app_update.py`, `scripts/build_amnezia_box_android.ps1`, runbooks.
- Docs and contracts: ADRs 096–116, `docs/overnight/*`, `packages/contracts/openapi.json`.
- Migration numbering: 27 migrations, no duplicate numbers (checked).

---

## 5. Session reports — claims versus the repo

Status key: **verified-in-repo** = the file, commit or constant is present and I checked it. **claimed-only** = reported, but no result file in the repo that I could read. **superseded** = a later run changed it. **contradicted** = repo or another report disagrees.

Reports read: `docs/overnight/OVERNIGHT_REPORT.md` (runs 1–4), `FINAL_REPORT.md`, `UNCOMMITTED_OWNER_REVIEW.md`, `FINAL_PLAN.md`, `PLAN.md`, `RUN3_PLAN.md`, `RUN3_OWNER_STEPS.md`, `ANDROID_VLESS_PLAN.md`, `ANDROID_VLESS_OWNER_STEPS.md`, `TASK7_handshake_rca.md`, `DEPLOY_RUNBOOK.md` (headings and results checked; the plan files were read for scope, not re-audited line by line).

| # | Claim (source) | Status | Evidence checked |
|---|---|---|---|
| 1 | 15 overnight commits `91a0633`…`b12fbb2` exist (FINAL) | verified-in-repo | `git log` on the branch |
| 2 | Nothing pushed (FINAL) | verified-in-repo | no `origin/claude/overnight-integration` in `git branch -r` |
| 3 | Nothing deployed / no server touched (all runs) | claimed-only | consistent with repo (no deploy artifacts committed); live state not checked, and not checked on purpose |
| 4 | ADRs 096–116 written (FINAL, OVERNIGHT) | verified-in-repo | all 21 files present under `docs/adr/` |
| 5 | Migrations `0026` (username) and `0027` (panel sources) (RUN3) | verified-in-repo | files present; no duplicate migration numbers (27 total) |
| 6 | Telegram username stored only from Mini App login (RUN3, ADR-110) | verified-in-repo (file) / claimed-only (behaviour) | migration present; behaviour tests not re-run |
| 7 | "Telegram username is not stored" (OVERNIGHT run 1) | **superseded** | replaced by ADR-110 in run 3 |
| 8 | FLAG_SECURE removed from both Android activities (OVERNIGHT run 1, ADR-103) | verified-in-repo | only comments remain in `ClientActivity.java` and `MainActivity.java` |
| 9 | Panel delivery off by default (FINAL decision 2, ADR-115) | verified-in-repo | `deliver_panel_servers: bool = False` in `config.py`; flag name `VPN_DELIVER_PANEL_SERVERS` in runbook |
| 10 | Panel VLESS session capped at 12 h on Windows (FINAL decision 3) | verified-in-repo | `kMaxVlessMs` documented in `apps/client/lib/panel_servers.dart` |
| 11 | Update manifest endpoints exist (OVERNIGHT run 1, FINAL T6) | verified-in-repo | `/api/v1/app/update-manifest` and `/api/v1/app/signed-update-manifest` in `main.py` |
| 12 | Ed25519 signed update **implemented but inactive** (FINAL T6) | verified-in-repo | `UPDATE_SIGNING_PUBLIC_KEY = ""` in `app_update.py`; Android `UpdateSignature.java` present; keygen script present. **With the empty key, signed updates are refused (fail closed).** |
| 13 | Android update installer, provider and checks (OVERNIGHT run 2) | verified-in-repo (files) / device not tested | `AppUpdateInstaller.java`, `UpdateApkProvider.java`, `app_update.dart` present |
| 14 | DNS choice on Android and Windows (OVERNIGHT run 2; run 1 said Windows deferred) | verified-in-repo (code); superseded for run 1 | `dns_choice.dart`, `DnsChoice.java`, `config_factory.cpp` (`IsDnsChoiceValue`, `dns_server`) |
| 15 | Orbit and bottom button share one 600 ms gate (OVERNIGHT run 1) | verified-in-repo | `debounce = 600 ms` in `primary_action.dart` |
| 16 | Diagnostic `Handshake:` line added (OVERNIGHT run 2, FINAL T8) | verified-in-repo | `diagnostic_report.dart`, `DiagnosticReportText.java` |
| 17 | Native handshake-seen memory on Android (FINAL T8) | verified-in-repo (code, test) | `HandshakeMemory.java` and `HandshakeMemoryTest.java` present |
| 18 | "Handshake corrupted" does not exist in code (OVERNIGHT run 2, RCA) | verified-in-repo | no match in `apps/`, `services/`, `scripts/`, `infra/` (only in docs) |
| 19 | Windows `with_utls` engine tag and `--vless-selftest` (FINAL T3, ADR-112) | verified-in-repo (code); engine self-test result claimed-only | `vless-selftest` in `config_tool.cpp`; `with_utls` in build scripts and `source-lock.json`; the test result itself appears only in FINAL |
| 20 | Run 3 said engine lacked `with_utls` (RUN3) | **superseded** by run 4 (`c7366d2`) | commit present |
| 21 | Android libbox **not bundled** in the APK (FINAL T5) | verified-in-repo | `build.gradle.kts` excludes `LibboxProviderV1.java` and `LibboxVpnService.java`; no `jniLibs` directory found |
| 22 | Subscription parser: verified link-list formats only, not wired to an endpoint (FINAL T7) | verified-in-repo (file, admin references); claimed-only (format verification, done from web sources) | `panel_sources.py` and admin/main references present |
| 23 | Gradle unit tests: 81 JUnit, 0 failures (FINAL) | verified-in-repo (local build output) | 16 `TEST-*.xml` files under the ignored `apps/client/build/` total tests=81, failures=0, errors=0. This is a local artifact, not committed. |
| 24 | Full API pytest: 1379 passed / 14 skipped / 0 failed (FINAL, OVERNIGHT run 4) | claimed-only | No pytest result file in the repo. Not re-run here. |
| 25 | `flutter test` 182 passed; `flutter analyze` clean; Windows ctest 2/2 (FINAL) | claimed-only | No result files found in the repo. Not re-run. |
| 26 | Release APK is **unsigned**, debug APK uses debug key (FINAL) | verified-in-repo (local artifacts exist) / signing claimed-only | `app-release.apk` and `app-release-unsigned.apk` exist under ignored build dir; signing state not re-checked |
| 27 | Windows zip `0.1.0-beta.26` built locally (FINAL) | verified-in-repo (local artifact exists) | `DetourVpn-windows-x64-0.1.0-beta.26.zip` in the ignored build dir |
| 28 | "No hunk written by you personally was found" in uncommitted work (UNCOMMITTED_OWNER_REVIEW) | **contradicted / unverifiable** | OVERNIGHT run 1 says the regenerated `openapi.json` "includes the owner's uncommitted API schema changes", and RUN 3 lists owner edits; those hunks are now inside committed mixed commits. Cannot be separated without history rewrite. |
| 29 | Co-Authored-By trailer names Opus 5.5 (FINAL) | verified-in-repo (commit messages) | The task asked for a Haiku line; this is only a trailer, not changed here. |
| 30 | Beta quota 5 GiB, ceiling 20 × 5 GiB (ADR-090 committed) | verified-in-repo | ADR-090 text on overnight. The untracked privacy draft says 512 MiB (stale, section 4). |

Tests that were **not** verified by this session: all pytest, flutter, Windows C++ and device results. Only the Gradle JUnit XML counts were checked against the repo, and only because they are local build outputs.

---

## 6. Conflicts and overlaps

- **Commit-level conflicts: none.** Every branch is an ancestor of `claude/overnight-integration`. A merge would be a fast-forward or a no-op for every local and remote branch.
- **Two-version file conflicts (uncommitted vs committed):**
  - `docs/beta-privacy-notice.md`: uncommitted draft in `beta-launch-checklist-bdf7e1` ≠ committed version on overnight. **Resolution proposed:** keep the committed version; the draft is stale (quota and log-retention wording). Owner decides.
- **Identical duplicates (no conflict):** the six I-030 files in `charming-grothendieck-f3d08c` are byte-identical to overnight's committed copies.
- **Mixed commits (not conflicts, but review hot spots):** per FINAL, these files carry more than one area and were committed in one area's commit: `ClientActivity.java`, `ClientApplication.java`, `config_factory.cpp/.h`, `config_factory_tests.cpp`, `openapi.json`, `db/models.py`, `test_migrations.py`, `AndroidManifest.xml`. Some intermediate commits may not build alone (FINAL says the tip builds).
- **Overlapping area with unpushed owner commits:** `codex/i025-android-windows` has 9 local commits not on origin (`53f9925`, `f912cf3` "owner working tree snapshot", I-030 and ADR-090/091 commits). They are included in overnight. Pushing overnight would publish them too.
- **Migration numbers:** no duplicates (27 files). Note the i025 "migration renumbered to 0019" commit; it is consistent with the current list.

---

## 7. Files not in any commit (owner files and local-only items)

These must stay out of any automated commit.

**Untracked (not in any commit on the branch they sit on):**
- `.claude/worktrees/beta-launch-checklist-bdf7e1/docs/beta-privacy-notice.md`: stale draft, differs from the committed copy. Keep out of commits; owner decides whether to delete after taking a backup.

**Ignored, machine-local (should never be committed):**
- `apps/android/local.properties`, `apps/client/android/local.properties` (SDK paths)
- `infra/ansible/inventory.yml`, `infra/ansible/release.yml`, `infra/beta/nodes.json` (inventory data; contents not printed here)
- `.claude/` (includes machine-local launch config)
- `.codex-remote-attachments/`, `.tgcloud/`, `apps/telegram-bot/.tgcloud/`
- `apps/client/.flutter-plugins-dependencies`, `apps/client/.metadata`, `apps/client/android/gradlew*`, `apps/client/android/gradle/`
- `chessy_training_game.pgn` (not a project file; in the main checkout only; left alone)
- `apps/telegram-mini-app/dist/`, `node_modules/`, `.venv/`, `__pycache__/`, `.cache/`, `build/`, `.ruff_cache/`, `.pytest_cache/`, `.mypy_cache/`, `.dart_tool/`
- `third_party/amnezia-box/amnezia-box-v1.260910.0-src.zip` (1.2 MB pinned source archive; ignored)
- Build outputs with unsigned APKs and a Windows zip: `apps/client/build/…` (ignored)

**Owner files committed on overnight but mixed with agent changes:** see section 6 (`openapi.json`, `db/models.py`, `ClientActivity.java`, etc.) and the 9 unpushed `codex/i025` commits `f912cf3` … `b3bbe9d` (not on origin).

---

## 8. Unresolved risks

1. **Signed updates are inactive.** The Ed25519 public key is empty in `app_update.py` and `UpdateSignature.java`. Until the owner runs the keygen script and pastes the public key (both places), the update path refuses signed manifests. No public update can be signed.
2. **Android update path needs `REQUEST_INSTALL_PACKAGES`** and a sideload model. On Google Play this must be removed or replaced (OVERNIGHT run 2, decision 6). Android 7–12 get no in-app update (fail closed).
3. **Screenshots and recents previews are allowed again** (`FLAG_SECURE` removed, ADR-103). This is a privacy protection removed on purpose; owner must accept it.
4. **Panel VLESS** is off by default. It requires `VPN_DELIVER_PANEL_SERVERS=true`, a Windows-only path, and a 12 h session cap. No Android VLESS: libbox is excluded from the APK.
5. **Subscription parser** is unit-level only and not wired to any endpoint.
6. **Telegram username** storage (ADR-110) is a privacy-relevant change. Its deletion path and log-redaction claims need the owner's review.
7. **Evidence and docs sanitization:** a count-only check found **54 files** under `docs/evidence/` and `docs/overnight/` that contain IPv4-shaped literals. I did not inspect them one by one (many may be version strings). **Before any push, scan them.** No private-key markers were found in the tree.
8. **Commit history is mixed.** Some commits do not build alone. Any selective publish or cherry-pick will be hard. Overnight contains owner snapshot commits (`f912cf3`, 102 files) and I-030 work as well.
9. **Beta privacy notice conflict** (section 4). The committed version is the one to keep; the stale draft could be restored by mistake.
10. **Version name is still `0.1.0-beta.26`.** Bump before any real release.
11. **Co-Authored-By trailers:** FINAL_REPORT says the overnight commits name Opus 5.5 (the run's model), not the Haiku line the task asked for. Not checked commit by commit, and not rewritten.

---

## 9. Device tests still needed (none done by any session)

**Android phone**
- In-app update on Android 13+: signed manifest accepted; tampered or unsigned refused; install over an older build. Android 12 or lower: no dialog.
- Install-from-unknown-sources screen flow.
- DNS choice actually used through the tunnel (after reconnect).
- Orbit and bottom button: one tap = one action (600 ms gate).
- Fault tolerance (ADR-096..099): screen off 60 min, swipe away, `am kill`, reboot, Wi-Fi ↔ mobile switches, airplane mode.
- Diagnostic report `Handshake:` line after a `lease_expired` with the screen on and off.
- Panel server row shows the Russian "not supported on Android" text and cannot be selected.
- AWG regression on the `with_utls`-tagged engine (AWG testers use the same engine).

**Windows PC**
- AWG connection still works with the new `with_utls` engine (regression).
- Panel VLESS server: listed, connects, exit address is the panel's, disconnect and reconnect after a network change, 12 h cap.
- DNS row: saving, refusal while connected, engine uses the chosen resolver.
- Diagnostic report copy includes the `Handshake:` line.

**Admin page (browser)**
- Admin list with Telegram ID column and username column (migrations 0026, 0027 on a test API).

**Mini App login** with a real Telegram test account that has a username, and again after removing it.

---

## 10. Method and limits

- Commands used were read-only: `git worktree list`, `git branch`, `git rev-parse`, `git status`, `git log`, `git rev-list --count`, `git merge-base`, `git diff`, `git show`, `git ls-tree`, `git grep`, `git cat-file`, `git status --ignored`, plus `stat`, `sha1sum`, `grep`, `ls`. No write, checkout, switch, stash, reset, clean, or commit was run.
- Hash comparisons were done on the untracked files against the committed copies.
- Test counts were checked only against local JUnit XML files. Other test results were not re-run and are marked claimed-only.
- Ignored secret-like files (inventory, local properties) were listed by name only; their contents were not printed.
- Not checked: device behaviour, server state, signing status of APKs, the content of each sanitization candidate in section 8.7.

---

## Русское резюме

- Работа по всем worktree и веткам сведена в одну линию: `claude/overnight-integration` (`b12fbb2`) содержит все коммиты остальных локальных и удалённых веток. Конфликтов между коммитами нет.
- Овернайт-работа — 15 коммитов (`91a0633`…`b12fbb2`) поверх `ddf1320`; всего 115 коммитов над `main`. Ничего не запушено на origin.
- Единственное реальное расхождение: `docs/beta-privacy-notice.md` в worktree `beta-launch-checklist-bdf7e1` — старый черновик с квотой 512 МиБ; в ветке актуальная версия с 5 ГиБ (решение владельца, ADR-090). Оставить закоммиченную версию.
- Файлы I-030 в worktree `charming-grothendieck-f3d08c` идентичны закоммиченным.
- Подпись обновлений (Ed25519) реализована, но публичный ключ пустой — подписанные обновления сейчас отвергаются.
- Тесты: Gradle JUnit (81, 0 падений) проверены по локальным отчётам; pytest, flutter test и Windows ctest — только заявлены в отчётах, не проверены.
- До пуша нужно: проверить 54 файла в docs на IP-подобные строки, решить про `FLAG_SECURE` и подпись обновлений, устройства не тестировались.
- Эта сессия ничего не слила, не закоммитила, не запушила и не развернула.
