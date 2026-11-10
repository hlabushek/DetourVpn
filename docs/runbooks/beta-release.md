# One-command beta release

For the owner: a regular PowerShell window in the project folder. You need free space on drive C: (at
least 4 GB) and a phone connected over USB if you want to install.

```powershell
.\scripts\release_beta.ps1 -Install
```

What the script does, in order, stopping at the first error:

1. **Checks:** Mini App tests and build, admin panel tests, admin panel contract tests.
2. **Deploy to the server:** admin panel and API (`deploy_beta_admin_panel.py`, restarts only the
   beta API, rolls back on its own if it fails), then the Mini App (`deploy_beta_mini_app.py`). Before each
   deploy a read-only check is made.
3. **APK:** signed build (`build_beta_apk.ps1`, asks for the keystore password twice, input is
   hidden; internally: formatting, analysis, tests, Android lint), signature and ID verification (report in
   `docs/evidence/i-030-beta-apk-verify-*.json`), prints the path and SHA-256, installs over the old
   version via `adb install -r` (with the `-Install` flag).

Flags: `-NoDeploy` (no server), `-NoApk` (no APK), `-Install` (install on the phone),
`-ApiOrigin` (the beta API by default).

The script refuses to run with uncommitted changes: the build always matches git.

## What to check on the phone after installing

1. DetourVPN icon and name.
2. Sign-in with the device code; the consent checkbox.
3. Three servers with flags; connecting; external IP; traffic usage.
4. Settings → «Приложения вне VPN» (Apps outside VPN): with VPN on, it offers to turn it off; the excluded
   app bypasses the VPN (check "my IP" in it and in the browser).
5. Settings → «Работа в фоне» (Background operation): allow operation without battery restrictions. Turn off the screen for a
   few minutes with VPN on: the connection must not drop («срок истёк», "expired").
6. Admin panel: sign-in, the «Приглашения» (Invitations) tab, inviting a second Telegram account up to the «Вошёл» (Signed in) status.

## Rollback

- Admin panel/API: the script rolls back the files and restarts the API on its own if the deploy fails;
  copies of the previous files are on the host in `/root/detourvpn-pre-admin-<id>`.
- Mini App: the previous release stays on the host; on failure the script restores the previous line in `api.env`.
- App: install the previous APK (`adb install -r` works with the same signing key and a
  versionCode that is not lower; when rolling back to a lower code, uninstall the app first).
