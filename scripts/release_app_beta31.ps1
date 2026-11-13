[CmdletBinding()]
param(
    # Required: HTTPS address where the release APK will be downloadable, for example
    # https://<PUBLIC_HOST>/api/v1/app/download/detourvpn-0.1.0-beta.31.apk
    [Parameter(Mandatory = $true)]
    [string]$ApkUrl,
    # Required: Russian release notes as a plain text file (shown in the update dialog).
    [Parameter(Mandatory = $true)]
    [string]$NotesFile,
    # Lowest version code that may still run. 1 = nobody is forced to update.
    [int]$MinSupportedVersionCode = 1,
    # Ed25519 private key for the update manifest. Must stay OUTSIDE the repository (ADR-107).
    [string]$KeyPath = (Join-Path $env:LOCALAPPDATA 'ProjectVpn\update-signing\detourvpn-update-signing.pem'),
    # Skip the version bump and commit (use when they are already done).
    [switch]$SkipVersionBump,
    # Do not install the APK on the USB-connected phone.
    [switch]$NoInstall
)
# Release 0.1.0-beta.31 (version code 32) of the Android client as an in-app update (ADR-107).
#
#   1. preflight: branch, no changes to tracked files other than the version files
#   2. bump the version in apps/client/pubspec.yaml and apps/client/android/app/build.gradle.kts
#      and commit ONLY those two files
#   3. signed APK through scripts/release_beta.ps1 -NoDeploy (asks for the keystore passwords,
#      hidden), optional install on the USB phone
#   4. sign the update manifest (scripts/prepare_app_update.py) into a folder outside the repo
#   5. print the upload command. It is NOT run here: publishing changes the beta host.
#
# Does NOT: deploy the server, run release_all.ps1 (that converts nodes to pool mode and
# disconnects users), push, or upload anything. Passwords are typed only into hidden prompts.
# NOT run by its author: read it before running.

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root

$VersionName = '0.1.0-beta.31'
$VersionCode = 32
$OldPubspec = '0.1.0-beta.30+31'
$OldVersionName = '0.1.0-beta.30'
$OldVersionCode = 31
$Branch = 'claude/connection-reliability'
$Pubspec = Join-Path $root 'apps\client\pubspec.yaml'
$Gradle = Join-Path $root 'apps\client\android\app\build.gradle.kts'
$Apk = Join-Path $root 'apps\client\build\app\outputs\flutter-apk\app-release.apk'
$OutDir = Join-Path $env:LOCALAPPDATA "ProjectVpn\updates\beta31"

function Step([string]$Title) { Write-Host ''; Write-Host "=== $Title ===" -ForegroundColor Cyan }
function Run([string]$Name, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "FAILED: $Name (exit code $LASTEXITCODE)" }
}

# ---- 1. preflight ------------------------------------------------------------
Step 'Preflight'
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne $Branch) { throw "Wrong branch '$branch'. Expected '$Branch'." }
if (-not (Test-Path -LiteralPath $NotesFile)) { throw "Notes file not found: $NotesFile" }
if (-not (Test-Path -LiteralPath $KeyPath)) { throw "Signing key not found outside the repo: $KeyPath (see ADR-107 key setup)" }
$keyInside = (Resolve-Path -LiteralPath $KeyPath).Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)
if ($keyInside) { throw 'The signing key is inside the repository. Move it out before releasing.' }
if (-not $ApkUrl.StartsWith('https://')) { throw 'ApkUrl must be HTTPS.' }

# Any modified tracked file blocks the release, except the two version files.
$modified = @(git diff --name-only | Where-Object { $_ -and $_ -notin @('apps/client/pubspec.yaml', 'apps/client/android/app/build.gradle.kts') })
if ($modified.Count -gt 0) {
    Write-Host 'Modified tracked files (commit or set aside first):' -ForegroundColor Yellow
    $modified | ForEach-Object { Write-Host "  $_" }
    throw 'Release blocked: uncommitted tracked changes would leak into the release.'
}
Write-Host "branch=$branch; tracked changes only in version files (or none)."

# ---- 2. version bump ---------------------------------------------------------
if (-not $SkipVersionBump) {
    Step "Version bump to $VersionName (code $VersionCode)"
    $pub = Get-Content -LiteralPath $Pubspec -Raw
    if ($pub -notmatch [regex]::Escape("version: $OldPubspec")) {
        throw "pubspec.yaml does not have 'version: $OldPubspec'. Check the current version before releasing."
    }
    $pub = $pub -replace [regex]::Escape("version: $OldPubspec"), "version: $VersionName+$($VersionCode)"
    [IO.File]::WriteAllText($Pubspec, $pub, (New-Object Text.UTF8Encoding($false)))

    $gr = Get-Content -LiteralPath $Gradle -Raw
    if ($gr -notmatch "versionCode = $OldVersionCode" -or $gr -notmatch "versionName = `"$([regex]::Escape($OldVersionName))`"") {
        throw "build.gradle.kts does not have versionCode $OldVersionCode and versionName $OldVersionName. Check before releasing."
    }
    $gr = $gr -replace "versionCode = $OldVersionCode", "versionCode = $VersionCode"
    $gr = $gr -replace "versionName = `"$([regex]::Escape($OldVersionName))`"", "versionName = `"$VersionName`""
    [IO.File]::WriteAllText($Gradle, $gr, (New-Object Text.UTF8Encoding($false)))

    Run 'git add version files' { git add -- 'apps/client/pubspec.yaml' 'apps/client/android/app/build.gradle.kts' }
    $msg = "chore: bump the client to $VersionName (code $VersionCode) for the beta.31 release`n`nCo-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>"
    Run 'git commit version bump' { git commit -m $msg -- 'apps/client/pubspec.yaml' 'apps/client/android/app/build.gradle.kts' }
    Write-Host 'Version bump committed (two files only).'
} else {
    Write-Host 'Version bump skipped (-SkipVersionBump).'
}

# ---- 3. signed APK -----------------------------------------------------------
Step 'Signed APK (keystore passwords are typed into hidden prompts)'
$doInstall = -not $NoInstall
Run 'release_beta.ps1 (NoDeploy)' { & (Join-Path $root 'scripts\release_beta.ps1') -NoDeploy -Install:$doInstall }
if (-not (Test-Path -LiteralPath $Apk)) { throw "Signed APK not found at $Apk" }
$apkItem = Get-Item -LiteralPath $Apk
$sha = (Get-FileHash -LiteralPath $Apk -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "apk=$($apkItem.FullName)"
Write-Host "apk_bytes=$($apkItem.Length)"
Write-Host "apk_sha256=$sha"

# ---- 4. signed manifest ------------------------------------------------------
Step 'Sign update manifest'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$apkCopy = Join-Path $OutDir ([IO.Path]::GetFileName($ApkUrl))
Copy-Item -LiteralPath $Apk -Destination $apkCopy -Force
Run 'prepare_app_update.py' {
    uv run --frozen python scripts/prepare_app_update.py `
        --apk $apkCopy `
        --version-code $VersionCode `
        --version-name $VersionName `
        --min-supported-version-code $MinSupportedVersionCode `
        --apk-url $ApkUrl `
        --notes-file $NotesFile `
        --out $OutDir `
        --signing-key $KeyPath
}
$manifest = Join-Path $OutDir 'app-update.json'
if (-not (Test-Path -LiteralPath $manifest)) { throw "Manifest not written: $manifest" }

# ---- 5. upload command (NOT run) --------------------------------------------
Step 'Next step for the owner (not run by this script)'
Write-Host "Manifest: $manifest"
Write-Host "APK copy: $apkCopy"
Write-Host ''
Write-Host 'Review the manifest, then check the upload with a dry run first:' -ForegroundColor Yellow
Write-Host "uv run --frozen python scripts/publish_beta_app_update.py --apk `"$apkCopy`" --manifest `"$manifest`""
Write-Host ''
Write-Host 'Only when the dry run looks right, add --execute to the same command. This writes to the beta host.' -ForegroundColor Yellow
Write-Host ''
Write-Host 'Phone test after install: docs/overnight/RELIABILITY2_DEVICE_TESTS.md (test A first).'
Write-Host ''
Write-Host "Done locally. Nothing was pushed, deployed, or uploaded. APK sha256=$sha"
