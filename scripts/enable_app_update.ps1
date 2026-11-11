[CmdletBinding()]
param(
    # Stage 1 (default): create the signing key, put the public key into the code, commit it,
    # build the signed APK with that key and install it on the USB phone.
    # Stage 2 (-Publish): sign an update manifest for the APK you already built.
    [switch]$Publish,
    # Where the APK will be downloadable from (HTTPS only), for example the beta host's download path.
    # Optional override. Default: the API host serves it at <ApiOrigin>/api/v1/app/download/<file>.
    [string]$ApkUrl = '',
    [string]$ApiOrigin = 'https://193.68.88.239',
    # Commit whose API files the host runs now. Pass it only if the API code (download route) is not
    # on the host yet; the script then deploys it first.
    [string]$ApiBaselineRev = '',
    # Lowest version code that may still run. 1 = nobody is forced to update.
    [int]$MinSupportedVersionCode = 1,
    [string]$NotesFile = '',
    [switch]$NoInstall,
    [switch]$Windows,
    [string]$KeyPath = (Join-Path $env:LOCALAPPDATA 'ProjectVpn\update-signing\detourvpn-update-signing.pem')
)
# In-app update, set up with one command (ADR-107).
#   .\scripts\enable_app_update.ps1            -> stage 1 (key, code, commit, signed build, install)
#   .\scripts\enable_app_update.ps1 -Publish -ApkUrl https://<host>/<path>/<file>.apk
#                                              -> stage 2 (signed app-update.json, upload steps printed)
# Passwords: the key passphrase (typed twice, hidden) and the keystore passwords of the APK build
# (hidden). Nothing is stored or printed. The private key stays OUTSIDE the repository; back it up
# offline. NOT run end to end by its author.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
function Step([string]$Title) { Write-Host ''; Write-Host "=== $Title ===" -ForegroundColor Cyan }
function Run([string]$Name, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "FAILED: $Name (exit code $LASTEXITCODE)" }
}
$apiFile = Join-Path $root 'services\api\src\vpn_api\app_update.py'
$javaFile = Join-Path $root 'apps\client\android\app\src\main\java\org\example\vpnpoc\UpdateSignature.java'
$apk = Join-Path $root 'apps\client\build\app\outputs\flutter-apk\app-release.apk'

if (-not $Publish) {
    Step 'Preflight'
    $dirty = @(git status --porcelain | Where-Object { $_ -notmatch '^\?\? (\.claude/|docs/evidence/)' })
    if ($dirty.Count -gt 0) { throw 'Uncommitted changes exist. Commit them first (the build must match git).' }
    $apiText = Get-Content -LiteralPath $apiFile -Raw
    if ($apiText -notmatch '(?m)^UPDATE_SIGNING_PUBLIC_KEY = ""\s*$') {
        throw 'A public key is already set in app_update.py. Nothing to do in stage 1; use -Publish.'
    }

    Step 'Signing key (outside the repository)'
    if (Test-Path -LiteralPath $KeyPath) {
        throw "A key already exists at $KeyPath but no public key is in the code. Stop: do not overwrite it. Move it away or set the public key by hand."
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $KeyPath) | Out-Null
    Write-Host 'You will type a passphrase (12+ characters) twice. Remember it: without it updates cannot be signed.'
    $keygenOutput = @()
    & uv run --frozen python scripts/app_update_keygen.py --private-key-out $KeyPath | Tee-Object -Variable keygenOutput | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'FAILED: key generation' }
    $publicLine = @($keygenOutput) | Where-Object { $_ -match '^public_key_b64=' } | Select-Object -First 1
    if (-not $publicLine) { throw 'Could not read the public key from the keygen output.' }
    $publicKey = ($publicLine -replace '^public_key_b64=', '').Trim()
    if ($publicKey -notmatch '^[A-Za-z0-9+/]{43}=$') { throw 'The public key has an unexpected format.' }

    Step 'Put the public key into the code (API and Android)'
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $apiNew = [regex]::Replace($apiText, '(?m)^UPDATE_SIGNING_PUBLIC_KEY = ""\s*$', "UPDATE_SIGNING_PUBLIC_KEY = `"$publicKey`"")
    [IO.File]::WriteAllText($apiFile, $apiNew, $utf8)
    $javaText = Get-Content -LiteralPath $javaFile -Raw
    if ($javaText -notmatch 'static final String PUBLIC_KEY_B64 = "";') { throw 'Java public key placeholder not found.' }
    $javaNew = $javaText.Replace('static final String PUBLIC_KEY_B64 = "";', "static final String PUBLIC_KEY_B64 = `"$publicKey`";")
    [IO.File]::WriteAllText($javaFile, $javaNew, $utf8)
    Run 'API test (key equality with the Java constant)' {
        uv run --frozen python -m pytest services/api/tests/test_app_update.py -q
    }
    git add -- $apiFile $javaFile
    git commit -m 'feat: set the in-app update public key (ADR-107)' -m 'Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>'
    if ($LASTEXITCODE -ne 0) { throw 'FAILED: commit of the public key' }

    Step 'Signed APK with the key (hidden keystore password prompts)'
    $releaseArgs = @{ NoDeploy = $true; WindowSeconds = 900 }
    if (-not $NoInstall) { $releaseArgs.Install = $true }
    if ($Windows) { $releaseArgs.Windows = $true }
    & (Join-Path $root 'scripts\release_beta.ps1') @releaseArgs
    if (-not $?) { throw 'FAILED: release build' }

    Step 'Stage 1 done'
    Write-Host "Private key: $KeyPath  <- BACK IT UP OFFLINE (a USB stick), and remember the passphrase."
    Write-Host 'This build can now verify signed updates. Install it by hand on every tester phone once.'
    Write-Host 'Next release: raise version: in apps/client/pubspec.yaml (name+code), rebuild, then run -Publish.'
    return
}

Step 'Publish: signed manifest'
if (-not (Test-Path -LiteralPath $apk)) { throw 'No APK found. Build it first (stage 1 or release_beta.ps1).' }
if (-not (Test-Path -LiteralPath $KeyPath)) { throw "Signing key not found at $KeyPath." }
$pubspec = Get-Content -LiteralPath (Join-Path $root 'apps\client\pubspec.yaml') | Where-Object { $_ -match '^version:\s*(\S+)\+(\d+)\s*$' } | Select-Object -First 1
if ($pubspec -notmatch '^version:\s*(\S+)\+(\d+)\s*$') { throw 'Could not read the version from pubspec.yaml.' }
$versionName = $Matches[1]; $versionCode = [int]$Matches[2]
$outDir = Join-Path $env:LOCALAPPDATA 'ProjectVpn\update-out'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$apkName = "DetourVpn-$versionName.apk"
if ([string]::IsNullOrWhiteSpace($ApkUrl)) { $ApkUrl = "$($ApiOrigin.TrimEnd('/'))/api/v1/app/download/$apkName" }
if ($ApkUrl -notmatch '^https://[^\s]+\.apk$') { throw 'ApkUrl must be an HTTPS link ending in .apk.' }
$apkCopy = Join-Path $outDir $apkName
Copy-Item -LiteralPath $apk -Destination $apkCopy -Force
if ([string]::IsNullOrWhiteSpace($NotesFile)) {
    $NotesFile = Join-Path $outDir 'notes.txt'
    [IO.File]::WriteAllText($NotesFile, "Обновление DetourVpn ${versionName}: исправления и улучшения.", (New-Object System.Text.UTF8Encoding($false)))
}
Write-Host "version=$versionName code=$versionCode min_supported=$MinSupportedVersionCode"
Write-Host 'The key passphrase is asked next (hidden).'
Run 'prepare manifest' {
    uv run --frozen python scripts/prepare_app_update.py --apk $apkCopy --version-code $versionCode --version-name $versionName --min-supported-version-code $MinSupportedVersionCode --apk-url $ApkUrl --notes-file $NotesFile --out $outDir --signing-key $KeyPath
}
$manifest = Join-Path $outDir 'app-update.json'

if (-not [string]::IsNullOrWhiteSpace($ApiBaselineRev)) {
    Step 'API code with the download route (only if it is not on the host yet)'
    Write-Host "baseline = $ApiBaselineRev (the commit whose API files the host runs now)"
    Run 'API deploy (dry run)' { uv run --frozen python scripts/deploy_beta_api_latest.py --baseline-rev $ApiBaselineRev --before-revision 0027_panel_server_sources }
    Run 'API deploy' { uv run --frozen python scripts/deploy_beta_api_latest.py --baseline-rev $ApiBaselineRev --before-revision 0027_panel_server_sources --execute }
}

Step 'Upload to the API host (dry run, then real)'
Run 'publish (dry run)' { uv run --frozen python scripts/publish_beta_app_update.py --apk $apkCopy --manifest $manifest }
$answer = Read-Host 'Type YES to upload the APK and manifest, set the API environment, restart the beta API and add one Caddy path line'
if ($answer -cne 'YES') { throw 'Cancelled. Nothing was changed on the server.' }
Run 'publish' { uv run --frozen python scripts/publish_beta_app_update.py --apk $apkCopy --manifest $manifest --execute }

Step 'Done'
Write-Host "Phones on a build that contains the public key (Android 13+) will now be offered $versionName."
Write-Host 'To check from a PC:  curl.exe -sk <ApiOrigin>/api/v1/app/signed-update-manifest'
