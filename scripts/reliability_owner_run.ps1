[CmdletBinding()]
param(
    # all = build -> publish -> phone -> logs, with a confirmation before each stage.
    [ValidateSet('all', 'build', 'publish', 'phone', 'logs')]
    [string]$Stage = 'all',
    # Install the signed APK over the USB phone right after the build (keeps app data).
    [switch]$Install,
    # Phone tests to run (numbers from docs/overnight/RELIABILITY_DEVICE_TESTS.md). Default: all 9.
    [int[]]$Tests = @(1, 2, 3, 4, 5, 6, 7, 8, 9),
    # How far back the server read-out goes (1..72 hours). Cover the drops you want explained.
    [ValidateRange(1, 72)]
    [int]$Hours = 48,
    # Folder of an earlier run to add to (phone results and server data go to the same place).
    [string]$RunDir = ''
)
# Connection reliability: everything the owner runs, in one place (docs/overnight/RELIABILITY_*.md).
#   .\scripts\reliability_owner_run.ps1                      -> all stages, asks before each
#   .\scripts\reliability_owner_run.ps1 -Stage build -Install
#   .\scripts\reliability_owner_run.ps1 -Stage publish
#   .\scripts\reliability_owner_run.ps1 -Stage phone -Tests 1,3 -RunDir <folder from a run>
#   .\scripts\reliability_owner_run.ps1 -Stage logs -Hours 24 -RunDir <folder from a run>
# build   : signed APK with 15-minute windows (release_beta.ps1 -NoDeploy -WindowSeconds 900). No server.
# publish : signed in-app update (enable_app_update.ps1 -Publish). Changes the beta API host only
#           after you type YES there.
# phone   : walks you through the phone tests and records your answers. Optional read-only adb
#           checks (installed version). Nothing is sent anywhere.
# logs    : read-only server read-out (scripts/beta_reliability_logs.py): IDs cut to 8 characters,
#           addresses masked. Nothing is changed on the server.
# Results go to %LOCALAPPDATA%\ProjectVpn\reliability\<time>\ (outside the repository and OneDrive).
# Passwords are typed only into the hidden prompts of the build and publish scripts.
# NOT run end to end by its author.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
function Step([string]$Title) { Write-Host ''; Write-Host "=== $Title ===" -ForegroundColor Cyan }
function Ask([string]$Question, [string[]]$Allowed) {
    while ($true) {
        $value = (Read-Host "$Question [$($Allowed -join '/')]").Trim().ToLowerInvariant()
        if ($Allowed -contains $value) { return $value }
        Write-Host "Please type one of: $($Allowed -join ', ')"
    }
}
function Confirm-Stage([string]$Name) {
    if ($Stage -ne 'all') { return $true }
    return (Ask "Run stage '$Name' now?" @('y', 'n')) -eq 'y'
}
function Clean-Line([string]$Value) {
    # Free text from the owner: one line, no addresses, links or long IDs; bounded length.
    $v = ($Value -replace '[\r\n|]', ' ').Trim()
    $v = $v -replace 'https?://\S+', '<link>'
    $v = $v -replace '\b(?:\d{1,3}\.){3}\d{1,3}\b', '<ip>'
    $v = $v -replace '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '<id>'
    if ($v.Length -gt 200) { $v = $v.Substring(0, 200) }
    return $v
}

# ---- run folder -----------------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($RunDir)) {
    $RunDir = Join-Path $env:LOCALAPPDATA ('ProjectVpn\reliability\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$phoneFile = Join-Path $RunDir 'phone-tests.md'
$serverFile = Join-Path $RunDir 'server-data.txt'
Write-Host "Results folder: $RunDir"

# ---- preflight ------------------------------------------------------------------------------------
Step 'Preflight'
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$pubspec = Get-Content -LiteralPath (Join-Path $root 'apps\client\pubspec.yaml') | Where-Object { $_ -match '^version:\s*(\S+)\+(\d+)\s*$' } | Select-Object -First 1
if ($pubspec -notmatch '^version:\s*(\S+)\+(\d+)\s*$') { throw 'Could not read the version from apps/client/pubspec.yaml.' }
$versionName = $Matches[1]; $versionCode = [int]$Matches[2]
Write-Host "branch=$branch version=$versionName code=$versionCode"
if ($branch -ne 'claude/connection-reliability') {
    Write-Host 'This is not the branch with the reliability fixes (claude/connection-reliability).' -ForegroundColor Yellow
    if ((Ask 'Continue anyway?' @('y', 'n')) -ne 'y') { throw 'Stopped. Nothing was changed.' }
}
$adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
function Installed-VersionCode {
    # Read-only adb query; $null if no single phone is connected.
    if (-not (Test-Path -LiteralPath $adb)) { return $null }
    # adb writes notes such as "daemon not running; starting now" to stderr; Windows PowerShell
    # turns those into errors under 'Stop', so this read-only query must not stop the script.
    $ErrorActionPreference = 'Continue'
    try {
        $devices = @(& $adb devices 2>$null | Where-Object { $_ -match "`tdevice\s*$" })
        if ($devices.Count -ne 1) { return $null }
        $line = & $adb shell dumpsys package com.detourvpn.client 2>$null | Select-String -Pattern 'versionCode=(\d+)' | Select-Object -First 1
        if ($line -and $line.Matches[0].Groups[1].Value) { return [int]$line.Matches[0].Groups[1].Value }
    } catch { }
    return $null
}

# ---- build ----------------------------------------------------------------------------------------
if (($Stage -in @('all', 'build')) -and (Confirm-Stage 'build')) {
    Step "Build the signed APK $versionName (15-minute windows; no server is touched)"
    $dirty = @(git status --porcelain | Where-Object { $_ -notmatch '^\?\? (\.claude/|docs/evidence/)' })
    if ($dirty.Count -gt 0) { throw 'Uncommitted changes exist. Commit them first (the build must match git).' }
    $releaseArgs = @{ NoDeploy = $true; WindowSeconds = 900 }
    if ($Install) { $releaseArgs.Install = $true }
    & (Join-Path $root 'scripts\release_beta.ps1') @releaseArgs
    if (-not $?) { throw 'FAILED: release build' }
    $apk = Join-Path $root 'apps\client\build\app\outputs\flutter-apk\app-release.apk'
    if (Test-Path -LiteralPath $apk) {
        $sha = (Get-FileHash -LiteralPath $apk -Algorithm SHA256).Hash.ToLowerInvariant()
        Add-Content -LiteralPath (Join-Path $RunDir 'build.txt') -Value "built=$(Get-Date -Format o) version=$versionName code=$versionCode apk_sha256=$sha"
        Write-Host "apk_sha256=$sha"
    }
}

# ---- publish --------------------------------------------------------------------------------------
if (($Stage -in @('all', 'publish')) -and (Confirm-Stage 'publish (in-app update on the beta API host)')) {
    Step "Publish $versionName as an in-app update"
    Write-Host 'The publish script shows a dry run first and changes the server only after you type YES.'
    Write-Host "Phones on code $($versionCode - 1) or lower (Android 13+) will be offered this build."
    & (Join-Path $root 'scripts\enable_app_update.ps1') -Publish
    if (-not $?) { throw 'FAILED: publish' }
}

# ---- phone tests ----------------------------------------------------------------------------------
$catalog = @{
    1 = @{ Name = 'Sleep 30 minutes'; Minutes = 30; Steps = @(
        'Connected and confirmed. Unplug the charger, lock the screen, put the phone down.',
        'After 30 minutes unlock it and open DetourVPN.',
        'Expected: VPN still on, or reconnected by itself within about a minute.') }
    2 = @{ Name = 'Sleep 2 hours'; Minutes = 120; Steps = @(
        'Same as test 1, for 2 hours (overnight is fine).',
        'Expected: as test 1.') }
    3 = @{ Name = 'App closed for a long time'; Minutes = 60; Steps = @(
        'Connected. Swipe DetourVPN away from the recent-apps screen.',
        'Check that the VPN key icon is still in the status bar.',
        'Use the phone normally or leave it locked for at least 1 hour, then open DetourVPN.',
        'Expected: the VPN stays on when the app opens; the app does not turn it off.') }
    4 = @{ Name = 'Switch server while connected'; Minutes = 0; Steps = @(
        'Connected to server A. Open the server list and pick server B.',
        'Expected: it disconnects from A and connects to B by itself within about 15 s. Then switch back to A.') }
    5 = @{ Name = 'Switch server while connecting'; Minutes = 0; Steps = @(
        'Disconnected. Tap Connect and, while it still says connecting, pick another server.',
        'Expected: it ends up connected to the server you picked last, with no error about the old connection.') }
    6 = @{ Name = 'Failing server, then retry'; Minutes = 0; Steps = @(
        'Pick a server you know is down (or turn off a node you control) and tap Connect.',
        'Expected: a clear Russian error. Note Code and Stage from the diagnostic report.',
        'Then pick a working server and tap Connect. Expected: connects on the first try.') }
    7 = @{ Name = 'Retry right after a disconnect'; Minutes = 0; Steps = @(
        'Connected. Tap Disconnect, then Connect immediately.',
        'Expected: it connects (maybe a few seconds slower), no error.') }
    8 = @{ Name = 'Reboot with the VPN on'; Minutes = 35; Steps = @(
        'Connected. Restart the phone and unlock it. Do not open the app.',
        'After 2 minutes: is the VPN key icon shown? (expected: yes)',
        'Lock the phone for 30 minutes without opening the app. Then check the key icon again.',
        'Then open DetourVPN. Expected: the VPN stays on; the app does not turn it off.') }
    9 = @{ Name = 'Wi-Fi to mobile data and back'; Minutes = 5; Steps = @(
        'Connected on Wi-Fi with the app open. Turn Wi-Fi off (mobile data on). Do not touch airplane mode.',
        'Open a web page every 10 s. Note how long until pages load again (expected: within about 20 s).',
        'While pages do not load, the status must NOT say connected; it should say the network changed and is restoring.',
        'Turn Wi-Fi back on and repeat. Then switch twice within 10 s and check it still recovers.') }
}
if (($Stage -in @('all', 'phone')) -and (Confirm-Stage 'phone tests')) {
    Step 'Phone tests (docs/overnight/RELIABILITY_DEVICE_TESTS.md)'
    if (-not (Test-Path -LiteralPath $phoneFile)) {
        Set-Content -LiteralPath $phoneFile -Encoding utf8 -Value @(
            "# Phone test results ($versionName, code $versionCode)", '',
            'Times are the PC clock (local, with UTC offset). No IDs, addresses or links are recorded.', '',
            '| # | test | start | end | result | code | stage | network | screen off | charger | installed code | notes |',
            '|---|---|---|---|---|---|---|---|---|---|---|---|')
    }
    Write-Host 'Before you start: battery setting for DetourVPN = Unrestricted; on Xiaomi/Samsung/Huawei also allow autostart.'
    Write-Host 'For any failure, open the diagnostic report in the app and read only the Code and Stage lines.'
    $installed = Installed-VersionCode
    if ($null -ne $installed) {
        Write-Host "Phone over USB: installed version code = $installed"
        if ($installed -lt $versionCode) { Write-Host "The phone is not on $versionName yet (code $versionCode). Update it first." -ForegroundColor Yellow }
    } else {
        Write-Host 'No single phone on USB (that is fine): the installed version is not checked.'
    }
    foreach ($number in $Tests) {
        if (-not $catalog.ContainsKey($number)) { Write-Host "No test number $number; skipped."; continue }
        $test = $catalog[$number]
        Step "Test $number - $($test.Name)"
        foreach ($line in $test.Steps) { Write-Host "  - $line" }
        if ($test.Minutes -gt 0) { Write-Host "  (this test takes about $($test.Minutes) minutes; the PC can stay on this prompt)" }
        if ((Ask 'Start this test now?' @('y', 'skip')) -eq 'skip') { continue }
        $start = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
        Write-Host "Started $start. Do the steps on the phone."
        [void](Read-Host 'Press Enter when the test is finished')
        $end = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
        $result = Ask 'Result' @('ok', 'reconnected', 'off', 'error', 'unclear')
        $code = ''; $stageName = ''
        if ($result -in @('off', 'error', 'unclear', 'reconnected')) {
            $code = Clean-Line (Read-Host 'Code from the diagnostic report (empty if none)')
            $stageName = Clean-Line (Read-Host 'Stage from the diagnostic report (empty if none)')
        }
        $network = Ask 'Network during the test' @('wifi', 'mobile', 'changed')
        $screen = Ask 'Screen off the whole time?' @('y', 'n', 'na')
        $charger = Ask 'Charger connected?' @('y', 'n')
        $notes = Clean-Line (Read-Host 'Notes (optional, no IPs or links)')
        $installedNow = Installed-VersionCode
        $installedText = if ($null -ne $installedNow) { "$installedNow" } else { '-' }
        Add-Content -LiteralPath $phoneFile -Encoding utf8 -Value "| $number | $($test.Name) | $start | $end | $result | $code | $stageName | $network | $screen | $charger | $installedText | $notes |"
        Write-Host "Saved to $phoneFile"
    }
}

# ---- server read-out ------------------------------------------------------------------------------
if (($Stage -in @('all', 'logs')) -and (Confirm-Stage "server read-out (read-only, last $Hours h)")) {
    Step "Server read-out, last $Hours hours (read-only)"
    Write-Host 'Runs fixed SELECT queries in a read-only session and reads the API journal. Changes nothing.'
    & uv run --frozen python scripts/beta_reliability_logs.py --hours $Hours --out $serverFile | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "The read-out ended with exit code $LASTEXITCODE; see $serverFile." -ForegroundColor Yellow }
    else { Write-Host "Saved to $serverFile" }
}

Step 'Done'
Write-Host "Results: $RunDir"
Write-Host 'Send phone-tests.md and server-data.txt. IDs are already shortened and addresses masked;'
Write-Host 'still look through them once before sending.'
