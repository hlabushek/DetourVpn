# Deployment runbook (morning) — overnight build 2026-11-11

Placeholders: `<SERVER_HOST>`, `<PUBLIC_HOST>`, `<ADMIN_TOKEN_FROM_SECURE_PROMPT>`, `<REPO_ROOT>`.
Steps marked **[SERVER]** change a server. Steps marked **[LOCAL]** do not. Do not paste secrets into chat.

## 0. Before anything [LOCAL]
```bash
cd <REPO_ROOT>
git status --short
git diff --stat
```
Nothing from the overnight run is committed. Commit only what you have reviewed.

## 1. Python checks [LOCAL]
```bash
.venv/Scripts/python.exe -m pytest services/api/tests/test_app_update.py services/api/tests/test_admin_ui_contract.py services/api/tests/test_health.py -q
.venv/Scripts/python.exe -m ruff check services/api/src services/api/tests scripts/prepare_app_update.py
.venv/Scripts/python.exe scripts/export_openapi.py --check
```
Expected: 33 passed; ruff clean; OpenAPI current.

PostgreSQL admin tests need a database and are NOT run by default:
```bash
# only if you have a disposable local PostgreSQL:
set VPN_TEST_DATABASE_URL=<LOCAL_TEST_DATABASE_URL>
.venv/Scripts/python.exe -m pytest services/api/tests/test_admin.py -q -k telegram_id
```

## 2. Client checks [LOCAL] — NOT run overnight
```bash
cd apps/client
flutter pub get
dart analyze
flutter test test/primary_action_test.dart test/dns_choice_test.dart
```
Android unit tests (need JDK 17 + Gradle):
```bash
cd apps/client/android
./gradlew testDebugUnitTest --tests "org.example.vpnpoc.DnsChoiceTest" --tests "org.example.vpnpoc.UpdatePolicyTest"
```

## 3. Build the client [LOCAL] — NOT run overnight
Use the existing script; do not change signing settings.
```powershell
.\scripts\build_flutter_client.ps1
```
Record the output path and `Get-FileHash -Algorithm SHA256 <apk-or-zip>` for your records.

## 4. Optional: in-app update manifest [LOCAL, then SERVER]
Only when you have a signed/uploaded APK you want to offer. The overnight build does **not** include
the download/installer code, so the app cannot install from the manifest yet. Prepare the manifest only:
```bash
.venv/Scripts/python.exe scripts/prepare_app_update.py \
  --apk <PATH_TO_RELEASE_APK> \
  --version-code <VERSION_CODE> --version-name <VERSION_NAME> \
  --min-supported-version-code <MIN_CODE> \
  --apk-url https://<PUBLIC_HOST>/downloads/<APK_FILE_NAME> \
  --notes-file <PATH_TO_RUSSIAN_NOTES.txt> \
  --out <OUTPUT_DIR>
```
Upload steps **[SERVER]** (run yourself; host paths are placeholders):
```bash
scp <OUTPUT_DIR>/app-update.json root@<SERVER_HOST>:/opt/detourvpn/updates/app-update.json
scp <PATH_TO_RELEASE_APK> root@<SERVER_HOST>:/opt/detourvpn/downloads/<APK_FILE_NAME>
```
Set the API environment variable **[SERVER]** to the manifest path, then restart the API service
(restart command depends on your unit name; placeholder below):
```bash
# in the API environment file on <SERVER_HOST>:
VPN_APP_UPDATE_MANIFEST_FILE=/opt/detourvpn/updates/app-update.json
systemctl restart <API_SERVICE_NAME>
```
Verify **[SERVER or LOCAL]**:
```bash
curl -s https://<PUBLIC_HOST>/api/v1/app/update-manifest
```
Expected: JSON with `apk_url`, `apk_sha256`. Without the variable: 404 `update_unavailable`.

Rollback **[SERVER]**: remove the `VPN_APP_UPDATE_MANIFEST_FILE` line, restart the API service. The endpoint
returns 404 again. Delete the uploaded files only after confirming nothing else uses them.

## 5. Admin panel [SERVER]
Deploy the updated `apps/admin/static/` files with the API as you normally do. Verify in the browser
after logging in with the admin credentials you enter yourself: the "Сортировка" dropdown is present,
and users with a linked Telegram ID show the column.

Rollback: redeploy the previous `apps/admin/static/` files. No database migration is involved.

## 6. Database [SERVER]
No migration was added overnight. Nothing to run.

## 7. Android app install [PHONE]
Install the built APK on a test phone. Then:
- Settings → "DNS для VPN": pick one, disconnect/reconnect, check connection.
- Connection screen: tap the orbit and the main button; both should do the same thing; a second quick tap should be ignored.
- Settings → confirm screenshots are now allowed (this was the owner's request).

## 8. Things NOT to do overnight-run items
- Do not deploy the Windows client without a physical test of the tunnel.
- Do not enable VLESS/Reality anything; it does not exist yet (ADR-108).
- Do not set the update manifest until the in-app installer is built (ADR-107 partial).
