[CmdletBinding()]
param(
    # Lowest version code that may still run. 1 = nobody is forced to update.
    [int]$MinSupportedVersionCode = 1,
    # Russian release notes shown in the update dialog. Default: the file written below.
    [string]$NotesFile = '',
    # Skip the version bump and commit (use when they are already done).
    [switch]$SkipVersionBump,
    # Skip the build: use the APK already built from HEAD (after a failure). Check its time first.
    [switch]$SkipBuild,
    # Do not install on the USB phone.
    [switch]$NoInstall,
    # Stop after the phone install. Publishing is a separate run with -PublishOnly, after your phone test.
    [switch]$PublishOnly
)
# Release 0.1.0-beta.34 (version code 35) of the Android client as an in-app update (ADR-107, ADR-150).
# Two stages, so the phone can be tested before anything reaches the testers:
#
#   Stage 1 (default):
#     1. preflight: branch, clean tree apart from the version files
#     2. bump the version to 0.1.0-beta.34+35 in pubspec.yaml and build.gradle.kts, commit those two
#     3. signed APK with 3600 s windows (hidden prompts: keystore password, key password), verify, install
#     4. stop and wait for your phone test
#   Stage 2 (-PublishOnly, after the test):
#     5. sign the update manifest (asks the update signing key passphrase, hidden)
#     6. dry run of the upload, then asks you to type YES before anything is uploaded
#
# Passwords: 2 in stage 1 (keystore and key), 1 in stage 2 (update signing passphrase). Nothing is stored.
# NOT run by its author: read it before running.
#
# Install note: if two devices are attached (emulator + phone), install by serial yourself:
#   adb devices -l
#   adb -s <serial> install -r "<APK path printed below>"

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root

$VersionName = '0.1.0-beta.34'
$VersionCode = 35
$OldPubspec = '0.1.0-beta.33+34'
$OldVersionName = '0.1.0-beta.33'
$OldVersionCode = 34
$WindowSeconds = 3600   # ADR-150: the server and the nodes accept 3600 s windows.
$Branch = 'claude/connection-reliability'
$Pubspec = Join-Path $root 'apps\client\pubspec.yaml'
$Gradle = Join-Path $root 'apps\client\android\app\build.gradle.kts'
$Apk = Join-Path $root 'apps\client\build\app\outputs\flutter-apk\app-release.apk'
$OutDir = Join-Path $env:LOCALAPPDATA 'ProjectVpn\updates\beta34'
$ApkName = "detourvpn-$VersionName.apk"
$KeyPath = Join-Path $env:LOCALAPPDATA 'ProjectVpn\update-signing\detourvpn-update-signing.pem'
# Same beta API origin as scripts/enable_app_update.ps1, read from there so it is typed only once.
$enableText = Get-Content -LiteralPath (Join-Path $root 'scripts\enable_app_update.ps1') -Raw
$ApiOrigin = [regex]::Match($enableText, "\[string\]\`$ApiOrigin = '([^']+)'").Groups[1].Value
if (-not $ApiOrigin) { throw 'Could not read ApiOrigin from scripts/enable_app_update.ps1.' }

function Step([string]$Title) { Write-Host ''; Write-Host "=== $Title ===" -ForegroundColor Cyan }
function Run([string]$Name, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "FAILED: $Name (exit code $LASTEXITCODE)" }
}

if (-not $NotesFile) {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $NotesFile = Join-Path $OutDir 'notes.txt'
    $notes = "Обновление DetourVpn ${VersionName}: более длинные окна доступа (до часа), реже переподключения; " +
        "улучшено восстановление связи после смены сети и короткого сна телефона."
    [IO.File]::WriteAllText($NotesFile, $notes, (New-Object Text.UTF8Encoding($false)))
    Write-Host "Notes written: $NotesFile (edit it if you want other text)"
}

if (-not $PublishOnly) {
    # ---- 1. preflight --------------------------------------------------------
    Step 'Preflight'
    $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($branch -ne $Branch) { throw "Wrong branch '$branch'. Expected '$Branch'." }
    $modified = @(git diff --name-only | Where-Object { $_ -and $_ -notin @('apps/client/pubspec.yaml', 'apps/client/android/app/build.gradle.kts') })
    if ($modified.Count -gt 0) {
        $modified | ForEach-Object { Write-Host "  modified: $_" -ForegroundColor Yellow }
        throw 'Release blocked: uncommitted tracked changes would leak into the release. Commit or set them aside.'
    }
    Write-Host "branch=$branch; no other tracked changes."

    # ---- 2. version bump -----------------------------------------------------
    if (-not $SkipVersionBump) {
        Step "Version bump to $VersionName (code $VersionCode)"
        $pub = Get-Content -LiteralPath $Pubspec -Raw
        if ($pub -notmatch [regex]::Escape("version: $OldPubspec")) { throw "pubspec.yaml does not have 'version: $OldPubspec'." }
        $pub = $pub -replace [regex]::Escape("version: $OldPubspec"), "version: $VersionName+$VersionCode"
        [IO.File]::WriteAllText($Pubspec, $pub, (New-Object Text.UTF8Encoding($false)))

        $gr = Get-Content -LiteralPath $Gradle -Raw
        if ($gr -notmatch "versionCode = $OldVersionCode" -or $gr -notmatch "versionName = `"$([regex]::Escape($OldVersionName))`"") {
            throw "build.gradle.kts does not have versionCode $OldVersionCode and versionName $OldVersionName."
        }
        $gr = $gr -replace "versionCode = $OldVersionCode", "versionCode = $VersionCode"
        $gr = $gr -replace "versionName = `"$([regex]::Escape($OldVersionName))`"", "versionName = `"$VersionName`""
        [IO.File]::WriteAllText($Gradle, $gr, (New-Object Text.UTF8Encoding($false)))

        Run 'git add version files' { git add -- 'apps/client/pubspec.yaml' 'apps/client/android/app/build.gradle.kts' }
        $msg = "chore: bump the client to $VersionName (code $VersionCode) and use 3600 s windows (ADR-150)`n`nCo-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>"
        Run 'git commit version bump' { git commit -m $msg -- 'apps/client/pubspec.yaml' 'apps/client/android/app/build.gradle.kts' }
        Write-Host 'Version bump committed (two files only).'
    } else {
        Write-Host 'Version bump skipped (-SkipVersionBump).'
    }

    # ---- 3. signed APK -------------------------------------------------------
    Step 'Signed APK with 3600 s windows (keystore password and key password, hidden)'
    if ($SkipBuild) {
        Write-Host 'Build skipped (-SkipBuild).'
    } else {
        # build_beta_apk.ps1 is called directly: release_beta.ps1 would pass the default 90 s window.
        Run 'build_beta_apk.ps1' {
            & (Join-Path $root 'scripts\build_beta_apk.ps1') -ApiOrigin $ApiOrigin -WindowSeconds $WindowSeconds
        }
    }
    if (-not (Test-Path -LiteralPath $Apk)) { throw "Signed APK not found at $Apk" }
    $sha = (Get-FileHash -LiteralPath $Apk -Algorithm SHA256).Hash.ToLowerInvariant()
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $apkCopy = Join-Path $OutDir $ApkName
    Copy-Item -LiteralPath $Apk -Destination $apkCopy -Force
    Write-Host "apk=$apkCopy"
    Write-Host "apk_sha256=$sha"

    if (-not $NoInstall) {
        Step 'Install on the USB phone (keeps app data)'
        $adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
        Run 'adb install' { & $adb install -r $apkCopy }
    }

    Step 'Stage 1 done: test on the phone first'
    Write-Host 'Test before publishing: connect; wait for the 3600 s window; switch Wi-Fi/LTE; then send the diagnostic report if anything fails.'
    Write-Host 'When the phone test is good, publish with:'
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\release_app_beta34.ps1 -PublishOnly"
    Write-Host 'Nothing was uploaded or pushed.'
    return
}

# ---- Stage 2: publish ----------------------------------------------------------
Step 'Publish: sign the update manifest (update signing passphrase, hidden)'
$apkCopy = Join-Path $OutDir $ApkName
if (-not (Test-Path -LiteralPath $apkCopy)) { throw "APK copy not found: $apkCopy. Run stage 1 first." }
if (-not (Test-Path -LiteralPath $KeyPath)) { throw "Update signing key not found: $KeyPath" }
# enable_app_update.ps1 -Publish: signs the manifest, dry-runs the upload, then asks you to type YES.
& (Join-Path $root 'scripts\enable_app_update.ps1') -Publish -NotesFile $NotesFile -MinSupportedVersionCode $MinSupportedVersionCode
if (-not $?) { throw 'FAILED: publish step (nothing was uploaded unless you typed YES)' }
Step 'Done'
Write-Host "Published $VersionName (code $VersionCode). Phones on an older build will be offered the update."
