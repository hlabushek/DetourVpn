# Closed beta: owner manual gates (checklist)

Status: preparation draft from ADR-090. **Not an invitation and not an authorization to deploy.**
Every step below is run by the owner on their own devices or accounts. Claude/Codex shells are
not a reliable proxy for the owner's Windows profile (ADR-077): run these in ordinary Windows
PowerShell from the project checkout. Record results as sanitized receipts under a *new*
filename in `docs/evidence/` (no Telegram IDs, OTPs, keys, passwords, IPs of testers, raw logs).

Order matters: each gate reduces risk for the next one.

## 1. Signing key restore and password recovery (no network needed)

Why: the beta APK can only be updated by the same key. A backup copy exists on the
BitLocker `E:` volume (`GERFFINSUSB`), but a restore was never tried.

1. Put the removable volume in, unlock BitLocker with the recovery method you would really use.
   If you cannot unlock it with the stored recovery key from the password manager, stop: that is
   the finding.
2. Copy the `.jks` from the volume to an **empty temporary folder** (not the live signing
   folder). Never overwrite `%LOCALAPPDATA%\ProjectVpn\beta-signing\detourvpn-beta.jks`.
3. Check the copy with the JDK `keytool -list -keystore <copy>` and enter the password from the
   password manager. Compare the SHA-256 fingerprint of the certificate with
   `signer_certificate_sha256` in `docs/evidence/i-030-beta-apk-20261101T172209Z.json`.
4. Delete the temporary copy.
5. Receipt fields: `date`, `bitlocker_unlock: pass|fail`, `keystore_password_from_manager:
   pass|fail`, `fingerprint_matches_receipt: true|false`. No passwords or paths with usernames.

## 2. Physical APK update over the installed beta

Why: `update_install` is `not_verified` in every APK receipt.

1. Build the *next* release with a higher `versionCode` using `scripts/build_beta_apk.ps1
   -ApiOrigin 'https://<approved API host>'`; verify with `scripts/verify_beta_apk.py` to a new
   receipt path (see `flutter-client.md`).
2. On the Pixel with the currently installed beta, install the new APK over it
   (`adb install -r`, or open the file). Do **not** uninstall first.
3. Pass only if: Android accepts it as an update, the app still shows the signed-in account and
   quota, and `adb shell pm list packages --show-versioncode com.detourvpn.client` shows the new
   code. Check the installed APK SHA-256 against the receipt.
4. Record `update_install: pass|fail`, old/new version codes, `session_kept: true|false`.

## 3. Natural scheduled backup and restore drill

Why: only an explicit task start and an import were seen; the 07:00 trigger and a restore were not.

1. After a morning with the PC on, check Task Scheduler `DetourVpn Beta Backup Daily`: last run
   time equals the scheduled time (not a manual start), result `0`, and a new `.enc` file is in
   the local backup folder. Record the last-run time and result code only.
2. If the PC was off at 07:00, note whether the task caught up (`StartWhenAvailable`); do not
   count a manual start as the natural trigger.
3. Restore drill (disposable PostgreSQL only; never the beta database):
   - with the portable PostgreSQL in `.cache/i-004/portable/pgsql/bin`, set
     `I027_VERIFY_ARCHIVE` to the newest `.enc`, `I027_VERIFY_KEY_FILE` to the local
     `backup.key`, `VPN_TEST_POSTGRES_BIN` to that `bin` folder, then run
     `uv run --frozen python scripts/test_with_postgres.py --postgres-bin <bin> -- services/api/tests/test_import_beta_backup.py -k owner_beta_archive`;
   - pass = the test passes (it does not print row data).
4. Record: `natural_trigger_observed`, `task_result`, `restore_drill: pass|fail`,
   `archive_age_hours`.

## 4. Support recipient and tester instructions

1. A private channel and the support contact `@DetourVpnHelper` were chosen on 2026-11-02 (see
   `docs/evidence/i-030-owner-ops-confirmation-20261102.json`); confirm they are still right and that
   the diagnostic share flow targets them. Decide the
   retention for support messages and for old encrypted backups (needed by ADR-091: deleted
   accounts stay in older backups until they expire). Write the chosen retention into
   ADR-090.
2. Read `beta-tester-quickstart.md` once as if you were the tester and mark any step that
   needs knowledge only you have.
3. Confirm `beta-tester-support.md` matches what you will actually answer.

## 5. First tester (I-026 friend onboarding)

Run only after gates 1-4 pass, with ADR-090's quota (512 MiB) configured in the beta
environment. Follow `closed-beta-first-tester.md` exactly, starting from
`docs/evidence/i-026-first-tester-run.template.json`. Start with one trusted tester, then the
2-3 person pilot. Stop on any ADR-090 stop criterion.

## 6. After the next API deploy: account deletion check

Requires deploying migration `0019_account_deletion` and the new API/APK, which this checklist
does not authorize. After the owner has deployed them with a normal backup beforehand:

1. With a throwaway second Telegram account that has a fresh invite, sign in, connect once,
   then tap «Удалить аккаунт» in Профиль and confirm.
2. Pass only if: the app returns to the login screen, the node confirms the peer was removed
   (ACK/cleanup), `reserved_bytes=0` and `revoke_gate=closed`, signing in again without a new
   invite returns `beta_invite_required`, and the user row is `deleted` with no identity row
   (read-only DB query by the owner).
3. Record booleans only.

## Stop

If any step fails, record the fixed failure stage and stop; do not retry by changing the
procedure. Open items that are not on this list: VPS plan and traffic limit (budget rule in
ADR-090), phase-6 measurements (usage lag, battery/memory, free-traffic cost), and the
independent egress check in `docs/beta-lte-first-connection-analysis-20261108.md`.
