[CmdletBinding()]
param(
    # Skip the server deploy (admin panel + API + Mini App).
    [switch]$NoDeploy,
    # Skip the signed Android build (asks for the keystore passwords).
    [switch]$NoApk,
    # After a successful build, install over the connected phone with adb (keeps app data).
    [switch]$Install,
    [string]$ApiOrigin = 'https://193.68.88.239'
)
# One command for a beta release:
#   1. checks (Mini App tests + build, admin tests, admin contract tests)
#   2. deploy: admin panel + API (restarts only the beta API, rolls back by itself) and the Mini App
#   3. signed APK: build, verify (receipt in docs/evidence), optional adb install
# Stops at the first failure. Passwords are typed only into the build step's hidden prompts.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
function Step([string]$Title) { Write-Host ''; Write-Host "=== $Title ===" -ForegroundColor Cyan }
function Run([string]$Name, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "FAILED: $Name (exit code $LASTEXITCODE)" }
}

Step 'Preflight'
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$dirty = @(git status --porcelain | Where-Object { $_ -notmatch '^\?\? \.claude/' })
if ($dirty.Count -gt 0) { throw 'There are uncommitted changes; commit or stash them first so the build matches git.' }
$free = [math]::Round((Get-PSDrive -Name ($root.Substring(0, 1))).Free / 1GB, 1)
Write-Host "branch=$branch; free_disk_gb=$free"
if ($free -lt 4) { throw 'Less than 4 GB free on the disk: free some space first (the build needs it).' }
Write-Host ('commit=' + (git rev-parse --short HEAD).Trim())

Step 'Checks'
Run 'Mini App tests' { npm --prefix apps/telegram-mini-app test }
Run 'Mini App build' { npm --prefix apps/telegram-mini-app run build }
Run 'Admin tests' { node --test apps/admin/tests/format.test.mjs }
Run 'Admin contract tests' { uv run --frozen python -m pytest services/api/tests/test_admin_ui_contract.py -q }

if (-not $NoDeploy) {
    Step 'Deploy: admin panel and API (read-only check first)'
    Run 'admin panel check' { uv run --frozen python scripts/deploy_beta_admin_panel.py }
    Run 'admin panel deploy' { uv run --frozen python scripts/deploy_beta_admin_panel.py --execute }
    Step 'Deploy: Mini App (read-only check first)'
    Run 'Mini App check' { uv run --frozen python scripts/deploy_beta_mini_app.py }
    Run 'Mini App deploy' { uv run --frozen python scripts/deploy_beta_mini_app.py --execute }
}

if (-not $NoApk) {
    Step 'Signed APK (you will be asked for the keystore password twice, input is hidden)'
    & (Join-Path $root 'scripts\build_beta_apk.ps1') -ApiOrigin $ApiOrigin
    if (-not $?) { throw 'FAILED: signed APK build' }
    $apk = Join-Path $root 'apps\client\build\app\outputs\flutter-apk\app-release.apk'
    if (-not (Test-Path -LiteralPath $apk)) { throw 'APK was not produced.' }
    $stamp = Get-Date -Format 'yyyyMMddTHHmmss'
    $receipt = Join-Path $root "docs\evidence\i-030-beta-apk-verify-$stamp.json"
    Run 'APK verification' {
        uv run --frozen python scripts/verify_beta_apk.py --apk $apk --expected-application-id com.detourvpn.client --output $receipt --sdk-root "$env:LOCALAPPDATA\Android\Sdk" --java-home 'C:\Program Files\Android\Android Studio\jbr'
    }
    $hash = (Get-FileHash -LiteralPath $apk -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "apk=$apk"
    Write-Host "apk_sha256=$hash"
    Write-Host "size_mb=$([math]::Round((Get-Item $apk).Length / 1MB, 1))"
    if ($Install) {
        Step 'Install over the connected phone (keeps app data)'
        $adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
        Run 'adb install' { & $adb install -r $apk }
    }
}

Step 'Done'
Write-Host 'All requested steps finished. Commit the new receipt in docs/evidence if you want it recorded.'
