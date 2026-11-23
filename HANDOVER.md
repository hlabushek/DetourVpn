# Handover — connection survivability plan

**Where we are:** Phases 0 to 6 are **CODE COMPLETE** and committed locally (ADR-144 to
ADR-149), plus the Phase 4 test gaps (`fd7ad24`). **The plan is NOT verified on a device.** There
was no APK build, no Gradle run of any kind, no device or phone run, and no Google Play policy
check. Every result comes from Dart tests, `flutter analyze` on an ASCII copy and the javac +
JUnit substitute for the Android unit tests. "Code complete" does not mean "working".

**Before any build:** Phase 6 changed `apps/android/patches/go-only.patch` and its pinned hash.
The prepared SDK tree in `.cache` (`sdk-go-only-c5-20261113`) is now stale; a build against it
fails the hash check by design. Prepare a fresh tree first (ADR-149 "Owner steps", step 1).

**One-shot owner build:** `scripts/run_overnight_build.ps1` does ADR-149 step 1 end to end. It
prepares `.cache\i-011\sdk-go-only-c6-20261117`, copies `jniLibs` and `assets` from c5, checks the
patch hash and both `libwg-go.so` files, asks for the API origin, keystore path and password
(hidden), then runs `scripts\build_flutter_client.ps1 -ApiOrigin <origin> -WindowTunnels`. At the
end it prints the release APK path and the `adb install -r` command. It stops at the first error.
Not run by the agents; the owner runs it. It has not been executed yet, so its first run is
unverified.

- Branch: `claude/connection-reliability` (worktree `.claude/worktrees/fixes-before-beta-0eeeba`),
  not pushed.
- Phases 0 and 1: commit `1fdbbf4`, ADR-144 (`docs/adr/144-native-connection-state-and-single-owner.md`).
- Phase 2 (initial connect hardening): commit `b5766b1`, ADR-145
  (`docs/adr/145-initial-connect-hardening.md`).
- Phase 4 (network roaming): the commit on top of `b5766b1`, ADR-146
  (`docs/adr/146-network-roaming.md`): 0.5 s action on a new network, 8 s loss confirmation,
  `setUnderlyingNetworks` after each establish, backoff reset on a returning network, two
  restarts per change then a new lease. The former gap (UI-started tunnels had no `protector`, so
  `setUnderlyingNetworks` was skipped) is closed in code by Phase 6.1 (ADR-149); not verified on a
  device.
- Phase 4 test gaps: commit `fd7ad24`. The network-callback decisions moved into the pure
  `NetworkCallbacks` helper (behaviour unchanged) with `NetworkCallbacksTest` (8 s loss timer,
  cancel on `onAvailable`, shorter loss). A TODO at the `restartInPlace` call site asks for a
  device check that `establish()` clears the underlying network and that the order is right.
- Phase 3 (surviving Doze): the commit after `fd7ad24`, ADR-147
  (`docs/adr/147-surviving-doze-overnight.md`):
  - 3.1 server: `MAX_WINDOW_SECONDS` 900 → 3600 (API contract, node gate `MAX_WINDOW_MS`, OpenAPI
    maximum, two server tests edited). **Not deployed and not tested** (pytest was not allowed in
    that task). The client still asks for 900 s; switching it is a later step after the API and
    every node agent are deployed.
  - 3.2 `RENEW_WINDOW` uses `setExactAndAllowWhileIdle` when exact alarms are allowed
    (`SCHEDULE_EXACT_ALARM`), renewing 10 min before expiry; otherwise the inexact fallback keeps
    the earlier ADR-142 margin. TODO: Play policy check by the owner before any Play release.
  - 3.3 `RecoveryCompanionService`, a foreground service only while the VPN is wanted and no
    tunnel is up.
  - 3.4 `BackstopJobService`, a 15-minute JobScheduler job (WorkManager was not added: not a
    dependency, and the Gradle lockfiles cannot be regenerated without Gradle).
  - 3.5 the only new wake lock is the backstop's, bounded to 10 s.
- Phase 5 (dead-peer and silent-drop detection): the commit after `8d46858`, ADR-148
  (`docs/adr/148-dead-peer-and-silent-drop-detection.md`). See the Phase 5 section below.
- Phase 6 (system integration): the commit after `5f1af00`, ADR-149
  (`docs/adr/149-lockdown-boot-and-update-integration.md`). See the Phase 6 section below.
- ADRs of this plan: ADR-144, ADR-145, ADR-146, ADR-147, ADR-148, ADR-149.
- Master plan: `C:\Users\JetGame\.claude\plans\pasted-content-id-2a60-act-as-prancy-nest.md`
  (audit, phases, owner decisions).
- Owner decisions (2026-11-17):
  - longer server window than 900 s: **yes** (Phase 3.1);
  - direct boot: **option (a)**, keep `BOOT_COMPLETED`, rely on Lockdown, document it;
  - automatic node failover on a confirmed dead peer: **yes** (Phase 5.2).
- Order of phases: 0 → 1 → 2 → 4 → 3 → 5 → 6 (all code complete locally; none device-verified).
- Nothing has been verified on a phone. Every client result so far comes from unit tests (Dart
  tests and the javac + JUnit substitute below), not from Gradle or a device. The Phase 3 server
  change has no test run at all.
- Open TODOs (owner-requested, in code and ADRs):
  - health probe URL is still `none` (`DEAD_PEER_PROBE_URL` in the manifest; ADR-148/149): needs a
    real project-owned https health endpoint before the probe can run;
  - the health probe may never run on Android (the app excludes its own package from the VPN;
    Android may refuse the bind, `bind_refused`): needs a device check and a routing decision
    (`TunnelProbeRunner`, manifest, ADR-148/149);
  - restart order after `restartInPlace` / `setUnderlyingNetworks` (ADR-146,
    `ClientApplication.restartTunnel`);
  - Play Store exact-alarm and `systemExempted` policy check (ADR-147, manifest,
    `NativeRenewer.schedule`).

## Tester bug fixes and audit (2026-11-21, autonomous run, committed locally, not pushed)
Every entry is **build/unit-test verified only**: no APK, no Gradle, no device. Java results are
the javac + JUnit substitute below (against the c6 SDK tree), not a Gradle pass.
- **ADR-151** (UI kept «Восстановление связи» ~20 s after the handshake): commit `<pending>`. Native
  state no longer waits 15.5 s for the watchdog to clear a proven restart; native re-checks the state
  each second for 30 s after window start / restart / network change; Dart renders events during a
  poll and the poll's first snapshot before its API call. Tests: Java 249 + 7 passed, `flutter test`
  318 passed, analyze only the `assets/orbit/` warning. Unverified on device: the time to
  «Подключено» after a Wi-Fi switch. (SHA filled in by the next commit: `fe99cc9`.)
- **ADR-152** (rare random drops): commit `b8659c3`. A 5 s timeout of the
  native renewal view (a busy worker, for example the installed-apps list) was read as "tunnel
  stopped" and ended native renewal; the window then expired. Now a timeout retries after 5 s, and
  the `apps` call runs off the tunnel worker. Tests: Java 250 + 7 passed (no Dart change).
  Unverified on device: renewal while the split-apps screen loads.
- **ADR-153** (rare random drops, second cause): commit `0c3fdc3`. The watchdog kept
  the old window's start, network trigger and spent restart budget when a new window started
  within 15 s of a stop, so the new window could be restarted or given up (`NEW_LEASE`) at its
  first tick. `stop()` and `prepareWindow()` now reset it; `beginWindow()` starts it. Tests: Java
  251 + 8 passed (new `ConnectionWiringTest`, run from `apps/client/android/app`). Unverified on
  device.
- **ADR-154** (connect needs retries): commit `612ac1a`. A tap on «Подключить» while
  a poll ran (on return to the app it reconciles, up to one API timeout) was silently ignored. It
  now waits for the poll (shown as busy) and then connects, unless the poll left a connection or a
  pending revoke. Tests: `flutter test` 320 passed, analyze only `assets/orbit/`. Unverified on
  device.
- **ADR-155** (cold start: «Повторить» useless until restart): commit `24acf69`. The
  native init (vault, device key, engine) ran once in `onCreate`; a failure left the engine null for
  the whole process. `ensureNative()` now retries it (at most every 2 s) before UI calls and before
  a restore engine starts; the device key is never replaced. Tests: Java 252 + 9 passed. Unverified
  on device; the tester's actual cause is not proven (look for `native_init_failed` in the event
  log). Open: a failing `sessionRead` at cold start still erases the session (see ADR-155).
- Stage 2 audit:
  - **ADR-156** (protected API relay): commit `e62fd6b`. A relayed connection whose
    client gave up on a black-holed upstream held one of the 8 relay slots until TCP aborted; eight
    blocked every restore-engine API call. The other direction now gets 15 s after the first ends.
    Tests: Java 253 + 9 passed. Unverified on device.
  - **ADR-157** (companion stop crash): commit `c51f5ca`. `stopService()` could reach
    the companion before its `startForeground()`, which Android reports as an app crash (taking the
    VPN service with it). The stop now waits until the companion runs in the foreground. Tests: Java
    254 + 10 passed. Unverified on device.
- Final checks of this run (after `c51f5ca`): Java substitute 254 + 10 passed; `flutter test` 320
  passed (last Dart change: ADR-154); `flutter analyze` on an ASCII copy: only the expected
  `assets/orbit/` warning; `dart format`: clean. The Java substitute now also runs
  `ConnectionWiringTest` from `apps/client/android/app` (it reads source files, like
  `SystemIntegrationWiringTest`).
- Found, not fixed at the time (all three fixed in the next run: ADR-158, ADR-159, ADR-160): a
  failing `sessionRead` at cold start erased the saved session; the server picker disabled every
  server while the last catalog refresh failed; handshake ages were wall-clock based.

## Known-issue fixes, API high availability and paranoid audit (2026-11-21 night run)
Autonomous run, committed locally, not pushed. Every entry is **unit-test verified only**: no APK,
no Gradle, no device, no real network or API failover. A commit's own SHA is filled in by the next
commit (a commit cannot contain its own hash).
- **ADR-158** (cold start erased the session on a read error): commit `5eac2dd`.
  A failing `sessionRead` now raises `session_storage_unavailable` and keeps the record; a refresh
  answered with anything but 401 keeps the token; `restore()` erases nothing itself and keeps the
  session after its bounded retry. Tests: `flutter test` 323 passed; `flutter analyze` (ASCII copy)
  only the `assets/orbit/` warning. Code complete, ready for build and device testing.
- **ADR-159** (server picker blocked after a failed refresh): commit `5a62b7c`.
  The last loaded list stays selectable; the failure is a non-blocking notice. Tests: `flutter test`
  324 passed; analyze only the `assets/orbit/` warning. Code complete, ready for build and device
  testing.
- **ADR-160** (wall-clock handshake ages): commit `e8bfd06`. The engine's
  `last_handshake_time_sec` is wall-clock (wireguard-go `time.Now()`); the new `HandshakeClock`
  anchors each new value to `elapsedRealtime()` once, and all three age call sites use it. Tests:
  Java substitute main run 262 passed + 11 from `apps/client/android/app` (no Dart change). Code
  complete, ready for build and device testing.
- **ADR-161** (fallback API origins): commit `9381f48`. New build value
  `VPN_API_FALLBACK_ORIGINS` (up to two https origins; `build_flutter_client.ps1
  -ApiFallbackOrigins`); empty keeps the single-origin behaviour. A transport failure moves the
  request to the next origin; after sending, only idempotent or keyed requests move; an HTTP answer
  never moves. Native renewal and the restore engine still use the primary only. Tests:
  `flutter test` 342 passed; analyze only the `assets/orbit/` warning. Code complete, ready for
  build and device testing.
- **ADR-162** (offline grace): commit `3fc90aa`. A renewal that cannot reach any
  API origin starts a grace that ends at the earlier of one hour and the lease's own deadline (never
  extends the lease); the home screen shows «Нет связи с сервером. VPN работает по текущему доступу
  ещё N мин». A tunnel that drops while offline restarts on its still-valid lease (max 3 per streak,
  foreground only). No brand-new connect offline. Tests: `flutter test` 353 passed; analyze only
  the `assets/orbit/` warning. Code complete, ready for build and device testing.
- **ADR-163** (API high-availability strategy): documentation only, commit `a959293`. Client parts are ADR-161/162; the server work (second instance on another provider,
  shared PostgreSQL with replica and failover, DNS failover/geo-steering, a health endpoint,
  database-level idempotency for money/reward operations, node-agent and native-path fallbacks) is
  **not built**, and needs owner decisions and approval. No server was touched.
- Paranoid audit:
  - **ADR-164** (a failed native stop escaped a poll or a connect as an unhandled error): commit
    `091b1b1`. `_cleanup` now handles it like the legacy controller
    (`native_disconnect_failed`, lease kept, nothing revoked; the next poll retries). Tests:
    `flutter test` 355 passed; analyze only the `assets/orbit/` warning. Code complete, ready for
    build and device testing.
  - **ADR-162 amendment** (the offline resume's cancelled branch had the same unguarded stop):
    commit `b575b98`. Caught now. Tests: `flutter test` 356 passed. Code complete, ready for build
    and device testing.
  - **ADR-165** (open items, comments only): the final commit of this run (its SHA is the branch
    tip). Three `TODO(paranoid-audit)` items: (1) no Dart-side timeout on native channel calls
    (`window_connection.dart` `refresh()`); (2) no overall deadline on a native renewal HTTP call
    (`NativeRenewer.call`); (3) the connected timer is wall-clock based (`connected_since.dart`).
- Final checks of this run: `flutter test` **356 passed**; `flutter analyze` (ASCII copy) only the
  expected `assets/orbit/` warning; `dart format` clean; Java substitute main run **262 passed** +
  **11** from `apps/client/android/app`. Not run: Gradle, APK build, device, backend tests (no
  server change), any real network or API failover.
- Not verified anywhere: device behaviour of every entry above, real keystore failures, real clock
  steps, real multi-origin failover and DNS, real API outages, node behaviour for an offline resume
  on the same lease, libbox.

## Server and node audit fixes (2026-11-23, NOT committed, not pushed, not deployed)
From the 2026-11-22 audit. Each entry is verified by local tests only (pytest on a temporary
local PostgreSQL via `scripts/test_with_postgres.py`); nothing was deployed or run on a node.
The two backup/restore fixtures in `test_window_service.py` and `test_window_funding.py` error
here because the portable PostgreSQL has no `pg_dump` (`postgres_tool_missing`), as before.
- **ADR-168** (C2, node stuck with more than 32 due commands): `poll_windows` answers at most
  32 commands (revokes first, then nearest deadline); the rest follow on the next poll.
  Contract unchanged. Tests: `test_audit_stage2_poll_commands.py` + window tests.
- Commits were refused by the permission system on 2026-11-23, so ADR-168 and every entry
  below are in the working tree only, uncommitted, for the owner to review and commit.
- **ADR-169** (H1, node daemon crashed on native errors and closed every gate): per-command
  containment in `tick()`, parse errors become `RuntimeFault`, the main loop retries every
  non-fatal error and exits only after 3 consecutive core identity failures. Unit unchanged.
  Tests: `test_audit_stage2_daemon.py` (H1 tests) + `test_window_daemon.py`,
  `test_window_runtime.py`, `test_window_pool.py`.
- **ADR-170** (M1, agent never re-registered after `window_runtime_mismatch`): HTTP error
  answers are reported as `rejected_<code>` (`[a-z_]{1,64}`, body capped at 4 KiB, else
  `control_rejected`); a runtime mismatch makes the loop register again. Tests: the M1 tests in
  `test_audit_stage2_daemon.py` + the same daemon test files.
- **ADR-171** (C1, critical: one rate bucket for every user behind Caddy): new setting
  `VPN_TRUSTED_PROXY_ADDRESS`; X-Forwarded-For trusted only from that peer; Caddy overwrites
  the header (`header_up X-Forwarded-For {remote_host}`); window routes limited per network
  (120/min), then per session family (30/min) or per connection id for native calls (30/min),
  each in its own transaction, no global bucket. **Owner step before it helps:** set
  `VPN_TRUSTED_PROXY_ADDRESS=127.0.0.1` in the API environment and install the new Caddyfile
  (see ADR-171). Tests: `test_audit_stage2_rate_limit.py` + auth, window, panel and config tests.
- **ADR-172** (H5, ufw "deny (routed)" silently dropped all slot traffic): the slot bootstrap
  preflight refuses a host whose other forward chain has `policy drop` unless both
  `ufw route allow` directions exist for every slot veth (`host_forward_policy_drops`); the
  runbook (`beta-add-node.md` step 1.4) and `install_beta_node.py` give the two rules as
  placeholders. No host firewall was touched. Tests: `test_audit_stage2_forward_policy.py` +
  bootstrap and install tests.
- **ADR-167 amendment** (L1, docs only): `authenticate()` already locked the user row first, so
  the `owned_connection` change was a no-op; the real opposite-order path was `token_connection`
  (native renew) against the node poll, fixed by ADR-167 decision 2. Unproven as the cause of
  the managed-database deadlocks (no log text). Test: `test_audit_stage2_lock_order.py`.
- The six entries above were committed by the owner (`2d13129` … `13edbee`).

## Remaining audit items (2026-11-23, stage 4, NOT committed, not deployed)
Working tree only; the owner commits item by item. Local tests only (temporary PostgreSQL);
nothing ran on a node or on the managed database.
- **Stale node-tooling tests** (no ADR): `test_beta_node_provisioning_cli.py` and
  `test_connect_beta_node.py` still expected the loopback-only checks that `1a4d252` (ADR-166)
  removed on purpose. The tests were wrong, not the scripts; they now check the ADR-166 rules
  (API parser, verify-full for remote, `vpn_api` role).
- **ADR-173** (M2): `poll_windows` isolates one bad receipt (savepoint) or one inconsistent
  connection (skipped) and logs `window_poll_item_failed code=<code>`; the node's other windows
  keep moving. Test: `test_window_poll_isolation.py`.
- **ADR-178** (M7 and M8): `last_seen_at` uses the database clock; the candidate check (run before
  every install) requires NTP synchronisation and a node clock within 1 s of the workstation
  (measured around the SSH call). Tests: `test_audit_stage4_clock.py`; `test_beta_nodes.py`
  fixture updated. Not run on a node.
- **ADR-177** (M4): `detourvpn-beta-api.service` uses `Wants=` (not `Requires=`) for the local
  database unit; `After=` kept. Test: `infra/ansible/tests/test_beta_api_unit_dependencies.py`.
  Not installed on any host; the owner confirms the database choice first.
- **ADR-176** (M3): `deploy_beta_api_latest.py` refuses before any change when the primary's
  `api.env` does not point at the local Docker database (`refused=database_not_local_docker`).
  Tests: `test_deploy_beta_guard.py`. A managed-database deploy needs its own runbook (owner).
- **ADR-175** (M9 and M5): `restore_primary_api.sh` and `activate_beta_caddy_allowlist.ps1` read
  host values from an operator file outside the repository; the allowlist script merges the
  node IP into the live Caddy `remote_ip` line (`scripts/caddy_allowlist.py`) instead of
  overwriting it. M9 is partial: about 47 other committed files still carry the identifiers.
  Tests: `test_caddy_allowlist.py`, `test_operator_scripts_config.py`.
- **ADR-174** (M10): `migrate_db_to_neon.sh` passes password-free conninfo strings
  (`scripts/pg_conninfo.py`) and a temporary 0600 `PGPASSFILE`; no URL on any command line.
  Test: `test_pg_conninfo.py`. Not run on a host.
- **ADR-179** (H4): new receipt observation `not_opened` (gate never opened: no charge, hold
  released, receipt kept). Node journal writes an `opening` marker before `gate.open()`;
  intent without it, late/skewed/declined grants and revokes of never-applied grants are
  `not_opened`; recovery stays `unknown`. Migration `0028_window_not_opened_receipts` (guarded
  rollback). **Rollout: migration + API first, then node agents.** Tests:
  `test_window_not_opened.py` + runtime/daemon/window/migration tests.
- **ADR-180** (H6): `operations.local_url` accepts a remote database only with explicit
  `sslmode=verify-full` and the API parser's rules; `pg_dump`/`pg_restore` get verify-full TLS
  settings; backup and maintenance units `Wants=` the local database. **Owner:** point
  `operations.env` at the managed database. Tests: `test_operations_remote_database.py` +
  operations/backup/maintenance tests.
- **ADR-181** (H3): migration `0029_window_hot_path_indexes` adds
  `peer_window_allocations(user_id)` and a partial `peer_connections(node_id, runtime_id)
  WHERE state <> 'closed'`; models declare both. The hold half of `runtime_ready` is still a
  history scan (owner decision). **Owner:** apply 0028 then 0029 on the managed database.
  Tests: `test_window_hot_path_indexes.py`, `test_migrations.py`.
- **ADR-182** (L2): link-code redeem checks the beta invite (FOR SHARE) before locking the user
  row and the code, the same order as `authenticate()` and the admin invite revoke. Test:
  `test_link_redeem_lock_order.py` (call order; invites not enforced in the harness).
- **ADR-183** (L3, L4, L5): first use of an access credential is marked before any row lock;
  a native renew token is checked on an unlocked read before the owner lock (re-checked under
  the locks); a `receipt_ack_required` grant is deferred on the node, not closed. Test:
  `test_lock_and_backlog_small_fixes.py`.
- **Deferred (client, need a phone and Gradle/Flutter builds):** L6 — native renewal treats a
  401 after token rotation as final (`apps/client/android/app/src/main/java/org/example/vpnpoc/NativeRenewer.java`
  `refused()`, lines 240-242, and the `finish()` call at 196/201); L7 — ADR-165 TODOs:
  no timeout on native channel calls (`apps/client/lib/window_connection.dart:1345`), no overall
  deadline on a native renewal call (`NativeRenewer.java:352`), wall-clock connected timer
  (`apps/client/lib/connected_since.dart:31`).
- Test environment note: the "known pg_dump errors" in this worktree only meant that its own
  `.cache` has no portable PostgreSQL. With `VPN_TEST_POSTGRES_BIN` set to the main checkout's
  `.cache/i-004/portable/pgsql/bin`, the backup/restore fixtures run and pass.

## Rules for the next agent
- One phase at a time, with one ADR per phase in `docs/adr/` (next free number: 184).
- Commit locally with the owner's OK. No push.
- No `flutter build`, no `gradlew assemble`, no release or deploy scripts, no adb or device tests,
  and no server changes without explicit approval.
- Allowed checks: `flutter test`, `flutter analyze` (see below), the Android unit tests (see below).
- Leave the untracked `docs/evidence/i-030-*.json` files and
  `docs/research/wg-tunnel-reconnect-comparison-20261116.md` alone. They are the owner's
  material.

## Known environment issues
- **Flutter is not on PATH.**
  - SDK: `%LOCALAPPDATA%\ProjectVpn\toolchains\flutter\bin\flutter.bat`.
  - Set `PUB_CACHE=%LOCALAPPDATA%\ProjectVpn\toolchains\pub-cache`.
  - JDK 21: `C:\Program Files\Android\Android Studio\jbr`. Android SDK 36:
    `%LOCALAPPDATA%\Android\Sdk`.
- **`flutter analyze` crashes on the Cyrillic path** (`OneDrive\Документы`, exit 255). Copy
  `apps/client/{lib,test,pubspec.yaml,pubspec.lock,analysis_options.yaml}` to an ASCII-only folder
  (for example the session scratchpad) and run it there. The only expected finding is the
  `assets/orbit/` warning, because that folder is not copied. Do not change code to work around
  the path.
- `flutter test` works in place: 315 tests passed at the Phase 5 and Phase 6 commits (310 before).
- **No Gradle wrapper in `apps/client/android` — on purpose, not generated.**
  - `scripts/build_flutter_client.ps1` builds from a temporary ASCII copy. It puts a wrapper there
    from `apps/android/gradle/wrapper/gradle-wrapper.jar` (SHA-256 pinned in the script) and
    `apps/client/gradle-wrapper.properties` (Gradle 8.14.3, distribution SHA pinned).
  - Adding a wrapper in place would not make `gradlew testDebugUnitTest` usable here, for two
    reasons:
    - the Flutter Gradle plugin needs the ASCII path;
    - `settings.gradle.kts` includes `:tunnel`, which builds the native amneziawg-go library.
      That is a full native build, which is outside the allowed checks.
  - Making Gradle unit tests runnable needs its own small task: a test-only script that copies to
    an ASCII path and runs `testDebugUnitTest` with the prebuilt tunnel. It needs the owner's OK.
- **Substitute for the Android unit tests** (used for ADR-140 to ADR-146; report it as a deviation,
  not a Gradle pass):
  1. Compile with `javac --release 17`:
     - sources: every `apps/client/android/app/src/main/java/**/*.java`, except the generated
       `GeneratedPluginRegistrant.java`, which is replaced by an empty stub;
     - plus `apps/android/app/src/main/java/**`, except `MainActivity`, `PocApplication`,
       `LibboxProviderV1` and `LibboxVpnService` (the same exclusions as Gradle);
     - plus the patched tunnel sources. Up to Phase 5 that was
       `.cache/i-011/sdk-go-only-c5-20261113/tunnel/src/main/java/**`. Since Phase 6 the patch
       differs from `c5`, so use a tree with the current patch applied (Phase 6 used a scratch
       copy: `git archive` of the pinned SDK revision from `.cache/i-011/awg-android`, then
       `git apply` of `go-only.patch`, outside the repository);
     - classpath:
       - `android-36/android.jar`;
       - `flutter_embedding_debug-1.0.0-*.jar`;
       - `annotation-jvm-1.9.1`;
       - `jsr305-2.0.1`;
       - `collection-1.1.0`;
       - `lifecycle-common-2.7.0`;
       - the `lifecycle-runtime-2.7.0` `classes.jar` (all from `~/.gradle/caches`).
  2. Run all tests in both `src/test/java` trees with JUnit 4.13.2 and hamcrest-core 1.3.
  3. Run `RestartInPlacePatchTest` and (since Phase 6) `SystemIntegrationWiringTest` from
     `apps/client/android/app`, because they read files relative to it.
  4. Expected result at the Phase 5 commit: 238 tests pass (235 in the main run plus 3
     `RestartInPlacePatchTest`). It was 194 at the Phase 4 commit, 202 after `fd7ad24` and 209
     at the Phase 3 commit. At the Phase 6 commit: 253 (246 in the main run plus 7 from
     `apps/client/android/app`: 3 `RestartInPlacePatchTest` + 4 `SystemIntegrationWiringTest`).
  5. The `find` results under Git Bash are POSIX paths; convert each jar with `cygpath -w`
     before joining the `;`-separated classpath for the Windows `javac.exe`.

## Phase 3 — surviving Doze overnight (recorded in ADR-147; committed locally)
What was run for it: the Java substitute (209 passed), `flutter test` (310 passed, no Dart
change) and `flutter analyze` on an ASCII copy (only the expected `assets/orbit/` warning).
What was **not** run or verified:
- no device run, no Doze or OEM-killer scenario;
- no Gradle build, manifest merge or lint;
- no server test run (pytest) and no deploy of API or node agents;
- no Play policy check for `SCHEDULE_EXACT_ALARM` / `USE_EXACT_ALARM` or the `systemExempted`
  foreground-service type.
Open follow-ups, each needing the owner's OK:
- run the server tests, then deploy the API and every node agent;
- only after that, let the client request windows longer than 900 s.
See ADR-147 "Known limitations" and "Owner steps".

## Phase 5 — dead-peer and silent-drop detection (recorded in ADR-148; committed locally)
- 5.1 `DataPathWatch` is now owned by the pure `DeadPeerWatch` and fed only from the native watch
  tick; `snapshot()` only reads it. The Dart `DataPathHealth` tracker stays: on Android it is never
  fed (native state is rendered first; a Dart test pins this), and it is still Windows' only rule.
- 5.2 Suspected stall (60 s outbound without inbound, or 180 s without a handshake) → one HTTPS
  probe through the VPN network (5 s). Pass = healthy. Two failures → restart in place, then a new
  lease (stop reason `dead_peer`), then failover only when opted in, then exhausted (the watchdog
  takes stalls back). `UNAVAILABLE` never escalates.
  - Health URL placeholder: `AndroidManifest.xml` meta-data
    `org.example.vpnpoc.DEAD_PEER_PROBE_URL` = `none` (probe off). No real endpoint anywhere.
  - Failover opt-in: meta-data `org.example.vpnpoc.DEAD_PEER_FAILOVER` = `false` (default off).
    When on, the restore engine uses the first other available AmneziaWG node for one window
    (`failoverRead`, `failoverNode`); the saved node is unchanged.
- 5.3 A captive portal (CAPTIVE_PORTAL, not VALIDATED) shows `RECOVERING{captive_portal}` with a
  sign-in hint; nothing restarts while it blocks traffic (the network is not usable).
What was run for it: the Java substitute (238 passed: 235 + 3), `flutter test` (315 passed) and
`flutter analyze` on an ASCII copy (only the expected `assets/orbit/` warning).
What was **not** run or verified:
- no device run; no silent-drop, captive-portal or failover scenario;
- **the probe may be unable to run on a phone at all**: the app excludes its own package from its
  VPN (ADR-132), so binding a socket to the VPN network is probably refused, which the code reports
  as `UNAVAILABLE` (inert, not harmful). A device run must check this first;
- no Gradle build, manifest merge or lint (the new meta-data was not checked by aapt);
- no real network probe behaviour (DNS, TLS, timeout) and no Play policy check.
See ADR-148 "Known limitations" and "Owner steps".

## Phase 6 — system integration (recorded in ADR-149; committed locally; CODE COMPLETE, NOT verified)
- 6.1 Lockdown is read only after `establish()` with the `AlwaysOnStatus` rule
  (`lockdownAfterRead`; read in `beginWindow()` and on the watch tick; the pre-establish read in
  `startAlwaysOnRecovery` is gone, R6). GoBackend now reports every service instance
  (`serviceCreated`, a `go-only.patch` change with a new pinned hash), so UI-started tunnels get a
  `protector` (`ServiceProtector`): native renewal under Lockdown and `setUnderlyingNetworks` work
  for them in code. The protector's uses are unchanged (ADR-140/141/146).
- 6.2 Option (a): `BOOT_COMPLETED` kept; not direct-boot aware; no device-protected token. Without
  Lockdown, traffic before the first unlock is unprotected (documented trade-off).
- 6.3 `MY_PACKAGE_REPLACED` added to `BootReceiver`, through the same pure gate as boot
  (`RestoreTrigger`): restores only when the user left the VPN connected and consent is ours.
What was run for it: the Java substitute against a scratch SDK tree with the new patch (253
passed: 246 + 7), `flutter test` (315 passed, no Dart change) and `flutter analyze` on an ASCII
copy (only the expected `assets/orbit/` warning).
What was **not** run or verified:
- no APK, no Gradle (no manifest merge, lint or `testDebugUnitTest`), no device run;
- no Lockdown, protector, reboot or app-update scenario on a phone;
- no Play policy check;
- the shared `.cache` prepared SDK tree was not regenerated (owner step, ADR-149).

## ADR-184 — native renewal HTTP call deadline (ADR-165 item 2, partly)

- `NativeRenewer.call` now disconnects a call after 25 s through a daemon watchdog thread. DNS is still unbounded (see ADR-184).
- Compiled `NativeRenewer.java` alone with the Android Studio JBR and android-36 jar: no errors. Not built into an APK, not run on a phone.

## ADR-185 — Dart-side timeouts on native channel calls (ADR-165 item 1)

- `WindowConnectionController` bounds every native call that poll, connect and disconnect await (20 s; tunnel starts 60 s). A timeout shows `native_timeout`; `_polling` and the ADR-154 connect wait always end.
- Late answers are dropped. While a call is unanswered, connect is refused and nothing new is sent to native except stops. A timed-out start is stopped at once and again after it answers; the next connect reconciles from a fresh snapshot. Dart only, no Java/C++ change.
- Verified: `flutter analyze` (no issues) and `flutter test` (362 passed, 6 new in `paranoid_audit_test.dart`) on an ASCII copy.
- Not verified: no APK, no Android/Windows build, no device run, no real hung native worker. `native_timeout` is not yet in the diagnostic report allowlists (Dart + `DiagnosticReportText.java`); reports show it as `unknown`. ADR-165 item 3 (wall-clock timer) not addressed: needs native.

## Release entry point (scripts/release.ps1)

- `-Stage Build`: preflight (clean tree, version read), signed APK via build_beta_apk.ps1, optional adb install, then stops for the phone test.
- `-Stage Publish`: enable_app_update.ps1 -Publish (manifest, dry run, typed YES).
- ApiOrigin is passed as a parameter for Build; no local settings file.
- Not automated on purpose: git commit/push, backend deploy and migrations 0028/0029, node rollout.
- Parsed only. Not run.
