<#
.SYNOPSIS
  Owner console for the 0.1.0-beta.37 release: window deploy, Android APK, phone test, Windows zip, publish.

.DESCRIPTION
  One menu for the steps in docs/runbooks/deploy-window-3600.md and docs/runbooks/beta-37-release.md.
  It only calls the existing scripts, in the required order:
    - refuses a step whose earlier step is not recorded as done;
    - asks for a typed YES (or PUBLISH) before every change to a server or a release;
    - records progress in %LOCALAPPDATA%\ProjectVpn\release-beta37\state.json (no addresses, no secrets).
  Passwords are never read here: the release scripts ask for them in their own hidden prompts.

  Run from the repository root:
    powershell -ExecutionPolicy Bypass -File .\scripts\beta37_release_console.ps1
  Read-only report and exit:
    powershell -ExecutionPolicy Bypass -File .\scripts\beta37_release_console.ps1 -Status
#>
[CmdletBinding()]
param(
    [switch]$Status,
    [string]$PostgresBin = ''
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $root
$env:PYTHONPATH = $root

$VersionName = '0.1.0-beta.37'
$VersionCode = 37
$Branch = 'claude/connection-reliability'
$Nodes = @('detourvpn-beta-helsinki', 'detourvpn-beta-germany', 'detourvpn-beta-netherlands')
$StateDir = Join-Path $env:LOCALAPPDATA 'ProjectVpn\release-beta37'
$ReceiptDir = Join-Path $StateDir 'receipts'
$StateFile = Join-Path $StateDir 'state.json'
$ApkCopy = Join-Path $env:LOCALAPPDATA "ProjectVpn\updates\beta37\detourvpn-$VersionName.apk"
$KeystorePath = Join-Path $env:LOCALAPPDATA 'ProjectVpn\beta-signing\detourvpn-beta.jks'
$UpdateKeyPath = Join-Path $env:LOCALAPPDATA 'ProjectVpn\update-signing\detourvpn-update-signing.pem'
$WindowsZip = Join-Path $root "apps\client\build\windows-zip\DetourVpn-windows-x64-$VersionName.zip"

# Values from the runbooks, verified 2026-11-17 (nodes, API) and 2026-11-25 (window tests).
# They are only defaults: check them with the read-only items before any change.
$script:State = @{
    SourceRev      = '8d46858'
    NodeBaseline   = '91a0633'
    ApiBaseline    = '06d6e94'
    BeforeRevision = '0027_panel_server_sources'
    Database       = ''
    SnapshotRef    = ''
    Gate0          = ''
    NodesDone      = @()
    ApiDeployed    = ''
    ApkBuilt       = ''
    PhoneTest      = ''
    WindowsZipSha  = ''
    WindowsLeak    = ''
    Published      = ''
}

function Write-Title([string]$Text) { Write-Host ''; Write-Host "=== $Text ===" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [ok]   $Text" -ForegroundColor Green }
function Write-Bad([string]$Text) { Write-Host "  [FAIL] $Text" -ForegroundColor Red }
function Write-Info([string]$Text) { Write-Host "  [info] $Text" }

function Load-State {
    if (-not (Test-Path -LiteralPath $StateFile)) { return }
    $saved = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    foreach ($p in $saved.PSObject.Properties) { $script:State[$p.Name] = $p.Value }
}

function Save-State {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    ($script:State | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $StateFile -Encoding UTF8
}

function Confirm-Typed([string]$Phrase, [string]$Question) {
    Write-Host $Question -ForegroundColor Yellow
    $answer = Read-Host "Type $Phrase to continue, anything else cancels"
    return ($answer -ceq $Phrase)
}

function Confirm-Yes([string]$Question) {
    $answer = (Read-Host "$Question (y/n)").Trim()
    return ($answer -match '^(y|yes)$')
}

function Invoke-Native([string]$Label, [string]$File, [string[]]$Arguments) {
    Write-Info ("> " + $Label)
    & $File @Arguments | Out-Host
    return ($LASTEXITCODE -eq 0)
}

function Get-ApiOrigin {
    # Same origin the release scripts use, read from the repository. Never printed.
    $text = Get-Content -LiteralPath (Join-Path $root 'scripts\enable_app_update.ps1') -Raw
    return [regex]::Match($text, "\[string\]\`$ApiOrigin = '([^']+)'").Groups[1].Value
}

function Resolve-PostgresBin {
    if ($PostgresBin -and (Test-Path -LiteralPath $PostgresBin)) { return $PostgresBin }
    if ($env:VPN_TEST_POSTGRES_BIN -and (Test-Path -LiteralPath $env:VPN_TEST_POSTGRES_BIN)) { return $env:VPN_TEST_POSTGRES_BIN }
    # A worktree has no .cache; the portable PostgreSQL lives in the main checkout.
    $common = (git rev-parse --path-format=absolute --git-common-dir 2>$null)
    if ($common) {
        $candidate = Join-Path (Split-Path -Parent $common.Trim()) '.cache\i-004\portable\pgsql\bin'
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $typed = (Read-Host 'Folder with PostgreSQL 17 tools (must contain initdb.exe), or Enter to cancel').Trim()
    if ($typed -and (Test-Path -LiteralPath $typed)) { return $typed }
    return ''
}

function Get-NextStep {
    if ($script:State.Gate0 -ne 'pass') { return '2  Window tests (Gate 0) must pass first' }
    $pending = @($Nodes | Where-Object { @($script:State.NodesDone) -notcontains $_ })
    if ($pending.Count -gt 0) { return '5  Update the next node agent (canary first)' }
    if (-not $script:State.ApiDeployed) { return '6 then 7  API preflight, then API deploy' }
    if (-not $script:State.ApkBuilt) { return '8  Build the signed APK and install it on the phone' }
    if ($script:State.PhoneTest -ne 'pass') { return '9  Phone test (must pass before publishing)' }
    if (-not $script:State.WindowsZipSha) { return '10 Windows zip (unsigned), optional for Android' }
    return '12 Publish the in-app update (typed PUBLISH)'
}

function Show-Status {
    Write-Title 'Repository'
    $branchNow = (git rev-parse --abbrev-ref HEAD).Trim()
    $head = (git rev-parse --short HEAD).Trim()
    if ($branchNow -eq $Branch) { Write-Ok "branch $branchNow at $head" } else { Write-Bad "branch $branchNow (expected $Branch)" }
    $tracked = @(git status --porcelain --untracked-files=no)
    if ($tracked.Count -eq 0) { Write-Ok 'no tracked changes' } else { Write-Bad "$($tracked.Count) tracked change(s): commit or set aside before a release" }
    git merge-base --is-ancestor $script:State.SourceRev HEAD | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok "source revision $($script:State.SourceRev) is in this branch" } else { Write-Bad "source revision $($script:State.SourceRev) is not in this branch" }

    Write-Title 'Version'
    $pub = Get-Content -LiteralPath (Join-Path $root 'apps\client\pubspec.yaml') -Raw
    if ($pub -match [regex]::Escape("version: $VersionName+$VersionCode")) { Write-Ok "pubspec.yaml $VersionName+$VersionCode" } else { Write-Bad 'pubspec.yaml is not at the beta.37 version' }
    $gradle = Get-Content -LiteralPath (Join-Path $root 'apps\client\android\app\build.gradle.kts') -Raw
    if ($gradle -match "versionCode = $VersionCode\b") { Write-Ok "build.gradle.kts versionCode $VersionCode" } else { Write-Bad 'build.gradle.kts versionCode is not 37' }

    Write-Title 'Tools and files'
    foreach ($tool in @('uv', 'python')) {
        if (Get-Command $tool -ErrorAction SilentlyContinue) { Write-Ok "$tool found" } else { Write-Bad "$tool not on PATH" }
    }
    $adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
    if (Test-Path -LiteralPath $adb) { Write-Ok 'adb found (Android SDK)' } else { Write-Bad 'adb not found in the Android SDK folder' }
    if (Test-Path -LiteralPath $KeystorePath) { Write-Ok 'beta signing keystore present' } else { Write-Info 'beta signing keystore not created yet (created on the first APK build)' }
    if (Test-Path -LiteralPath $UpdateKeyPath) { Write-Ok 'update signing key present' } else { Write-Bad 'update signing key missing: publishing is impossible until it is restored' }
    if (Test-Path -LiteralPath $ApkCopy) { Write-Info 'a beta.37 APK copy exists from an earlier build; item 8 rebuilds it from HEAD' } else { Write-Info 'beta.37 APK not built yet' }
    if (Test-Path -LiteralPath $WindowsZip) { Write-Ok 'Windows zip present' } else { Write-Info 'Windows zip not built yet' }
    $bin = Resolve-PostgresBinQuiet
    if ($bin) { Write-Ok 'PostgreSQL tools found for the window tests' } else { Write-Info 'PostgreSQL tools not found (item 2 asks for the folder)' }

    Write-Title 'Recorded progress (state file)'
    Write-Info "Gate 0 (window tests): $(if ($script:State.Gate0) { $script:State.Gate0 } else { 'not run' })"
    Write-Info ("Nodes updated: " + (@($script:State.NodesDone).Count) + " of " + $Nodes.Count + " (" + (@($script:State.NodesDone) -join ', ') + ")")
    Write-Info "API deployed: $(if ($script:State.ApiDeployed) { $script:State.ApiDeployed } else { 'no' })"
    Write-Info "APK built and installed: $(if ($script:State.ApkBuilt) { $script:State.ApkBuilt } else { 'no' })"
    Write-Info "Phone test: $(if ($script:State.PhoneTest) { $script:State.PhoneTest } else { 'not recorded' })"
    Write-Info "Windows leak test (ADR-196): $(if ($script:State.WindowsLeak) { $script:State.WindowsLeak } else { 'not recorded' })"
    Write-Info "Published: $(if ($script:State.Published) { $script:State.Published } else { 'no' })"
    Write-Info "Database mode for the API: $(if ($script:State.Database) { $script:State.Database } else { 'NOT CHOSEN (item S)' })"

    Write-Title 'API reachability (read-only)'
    $origin = Get-ApiOrigin
    if ($origin) {
        try {
            $response = Invoke-WebRequest -Uri ($origin + '/api/v1/ready') -UseBasicParsing -TimeoutSec 15
            Write-Ok "API /ready answers HTTP $($response.StatusCode)"
        } catch {
            Write-Info 'API /ready did not answer from this PC (network or certificate). Check on the host.'
        }
    } else {
        Write-Bad 'API origin not found in scripts/enable_app_update.ps1'
    }

    Write-Title 'Next step'
    Write-Host "  $(Get-NextStep)" -ForegroundColor Yellow
}

function Resolve-PostgresBinQuiet {
    if ($PostgresBin -and (Test-Path -LiteralPath $PostgresBin)) { return $PostgresBin }
    if ($env:VPN_TEST_POSTGRES_BIN -and (Test-Path -LiteralPath $env:VPN_TEST_POSTGRES_BIN)) { return $env:VPN_TEST_POSTGRES_BIN }
    $common = (git rev-parse --path-format=absolute --git-common-dir 2>$null)
    if ($common) {
        $candidate = Join-Path (Split-Path -Parent $common.Trim()) '.cache\i-004\portable\pgsql\bin'
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return ''
}

function Invoke-Gate0 {
    $bin = Resolve-PostgresBin
    if (-not $bin) { Write-Bad 'No PostgreSQL folder given. Nothing ran.'; return }
    $tests = @(Get-ChildItem -LiteralPath (Join-Path $root 'services\api\tests') -Filter 'test_window_*.py' |
        ForEach-Object { 'services/api/tests/' + $_.Name })
    # The operations tests read this variable directly; --postgres-bin alone is not enough.
    $env:VPN_TEST_POSTGRES_BIN = $bin
    $uvArgs = @('run', '--frozen', '--python', '3.13', 'python', 'scripts/test_with_postgres.py', '--postgres-bin', $bin, '--') + $tests + @('-q')
    $passed = Invoke-Native 'window tests on a disposable PostgreSQL (no beta access)' 'uv' $uvArgs
    Remove-Item Env:\VPN_TEST_POSTGRES_BIN -ErrorAction SilentlyContinue
    if ($passed) {
        $script:State.Gate0 = 'pass'
        Write-Ok 'Gate 0 passed.'
    } else {
        $script:State.Gate0 = 'fail'
        Write-Bad 'Gate 0 failed. Do not deploy. Read the output above.'
    }
    Save-State
}

function Invoke-NodeDryRunAll {
    foreach ($node in $Nodes) {
        Invoke-Native "dry run $node (changes nothing)" 'python' @('-m', 'scripts.update_beta_node_agent', '--node', $node, '--source-rev', $script:State.SourceRev, '--baseline-rev', $script:State.NodeBaseline) | Out-Null
    }
}

function Invoke-NodeBaselineCheck {
    foreach ($node in $Nodes) {
        Invoke-Native "baseline check $node (read-only)" 'python' @('-m', 'scripts.update_beta_node_agent', '--node', $node, '--baseline-rev', $script:State.NodeBaseline, '--check-baseline') | Out-Null
    }
}

function Invoke-UpdateNextNode {
    $pending = @($Nodes | Where-Object { @($script:State.NodesDone) -notcontains $_ })
    if ($pending.Count -eq 0) { Write-Ok 'All nodes are already recorded as updated.'; return }
    $node = $pending[0]
    Write-Info "Next node in order: $node (the first one is the canary)"
    if (-not (Invoke-Native "dry run $node (changes nothing)" 'python' @('-m', 'scripts.update_beta_node_agent', '--node', $node, '--source-rev', $script:State.SourceRev, '--baseline-rev', $script:State.NodeBaseline))) {
        Write-Bad 'Dry run failed. Nothing changed.'
        return
    }
    $question = "This restarts the node agent on $node and drops its current connections. Clients reconnect by themselves. Run only when few people are connected. Read the dry run above first."
    if (-not (Confirm-Typed 'YES' $question)) { Write-Info 'Cancelled. Nothing changed.'; return }
    if (-not (Invoke-Native "update $node" 'python' @('-m', 'scripts.update_beta_node_agent', '--node', $node, '--source-rev', $script:State.SourceRev, '--baseline-rev', $script:State.NodeBaseline, '--execute'))) {
        Write-Bad 'Update failed. The script restores the old files on failure. Check the output above before trying again.'
        return
    }
    if (Confirm-Yes "Is $node online in the admin panel, and does a test phone connect to it?") {
        $script:State.NodesDone = @($script:State.NodesDone) + $node
        Save-State
        Write-Ok "$node recorded as updated."
    } else {
        Write-Bad "$node is NOT recorded as done. Fix it first. The API step stays blocked until all nodes are done."
    }
}

function Get-ApiArgs([switch]$Execute) {
    $list = @('scripts/deploy_beta_api_latest.py', '--source-rev', $script:State.SourceRev, '--baseline-rev', $script:State.ApiBaseline, '--before-revision', $script:State.BeforeRevision)
    if ($script:State.Database -eq 'managed') {
        $list += @('--database', 'managed', '--snapshot-ref', $script:State.SnapshotRef)
    } else {
        $list += @('--database', 'local')
    }
    if ($Execute) { $list += '--execute' }
    return $list
}

function Test-ApiDatabaseChosen {
    if ($script:State.Database -ne 'local' -and $script:State.Database -ne 'managed') {
        Write-Bad 'Choose the API database mode first (item S). The scripts default to local Docker, so check which one the beta API really uses.'
        return $false
    }
    if ($script:State.Database -eq 'managed' -and -not $script:State.SnapshotRef) {
        Write-Bad 'Managed mode needs the snapshot id you created in the provider console (item S).'
        return $false
    }
    return $true
}

function Invoke-ApiPreflight {
    if (-not (Test-ApiDatabaseChosen)) { return }
    Invoke-Native 'API preflight (no change)' 'python' (Get-ApiArgs) | Out-Null
}

function Invoke-ApiDeploy {
    $pending = @($Nodes | Where-Object { @($script:State.NodesDone) -notcontains $_ })
    if ($pending.Count -gt 0) { Write-Bad "Nodes not yet recorded as updated: $($pending -join ', '). Update the nodes first (item 5)."; return }
    if (-not (Test-ApiDatabaseChosen)) { return }
    Write-Info 'Preflight first (read-only):'
    if (-not (Invoke-Native 'API preflight (no change)' 'python' (Get-ApiArgs))) { Write-Bad 'Preflight failed. Nothing changed.'; return }
    if (-not (Confirm-Typed 'YES' 'Deploy the API to the beta host. The script takes a database dump first and restores the old code on failure.')) { Write-Info 'Cancelled. Nothing changed.'; return }
    if (Invoke-Native 'API deploy' 'python' (Get-ApiArgs -Execute)) {
        $script:State.ApiDeployed = (Get-Date).ToString('yyyy-MM-dd')
        Save-State
        Write-Ok 'API deployed. Now check /api/v1/ready (item S/status) and a 3600 s window from a test phone.'
    } else {
        Write-Bad 'API deploy failed. The script restores the old code and database dump automatically. Read the output above.'
    }
}

function Invoke-ApkStage1 {
    if ($script:State.Gate0 -ne 'pass') { Write-Bad 'Gate 0 (item 2) must pass before building.'; return }
    if (-not (Confirm-Typed 'YES' 'Build the signed APK from the committed HEAD and install it on the USB phone. The script asks for the keystore and key passwords itself.')) { Write-Info 'Cancelled.'; return }
    try {
        & (Join-Path $root 'scripts\release_app_beta37.ps1')
        $script:State.ApkBuilt = (Get-Date).ToString('yyyy-MM-dd')
        Save-State
        Write-Ok 'APK built and installed (stage 1). Note the apk_sha256 printed above.'
    } catch {
        Write-Bad ("APK stage failed: " + $_.Exception.Message)
    }
}

function Invoke-PhoneTest {
    if (-not $script:State.ApiDeployed) { Write-Info 'Warning: the API is not recorded as deployed. A 3600 s window will not be granted yet.' }
    $checks = @(
        'Connect works and the home screen shows the connected state',
        'The connection stays up through a full 3600 s window (about one hour)',
        'Switching Wi-Fi to LTE and back reconnects without a manual reset',
        'Home-screen states match the changelog text',
        'Navigation bar colour is right in light and dark theme'
    )
    Write-Info 'Answer for the real phone test. Record only pass or fail. No IPs or identifiers.'
    $results = @()
    $allOk = $true
    foreach ($check in $checks) {
        $passed = Confirm-Yes $check
        if (-not $passed) { $allOk = $false }
        $results += [pscustomobject]@{ check = $check; passed = $passed }
    }
    $result = 'fail'
    if ($allOk) { $result = 'pass' }
    New-Item -ItemType Directory -Force -Path $ReceiptDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $receipt = [pscustomobject]@{ version = $VersionName; date = (Get-Date).ToString('s'); result = $result; checks = $results }
    $path = Join-Path $ReceiptDir "phone-$stamp.json"
    $receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $path -Encoding UTF8
    $script:State.PhoneTest = $result
    Save-State
    if ($allOk) { Write-Ok "Phone test PASS recorded: $path" } else { Write-Bad "Phone test FAIL recorded: $path. Do not publish. Send the diagnostic report from the app." }
}

function Invoke-WindowsZip {
    try {
        & (Join-Path $root 'scripts\build_windows_client.ps1') -BuildEngine
    } catch {
        Write-Bad ("Windows build failed: " + $_.Exception.Message)
        return
    }
    if (-not (Test-Path -LiteralPath $WindowsZip)) { Write-Bad 'Zip not found after the build.'; return }
    New-Item -ItemType Directory -Force -Path $ReceiptDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $out = Join-Path $ReceiptDir "windows-zip-$stamp.json"
    $sha = (Get-FileHash -LiteralPath $WindowsZip -Algorithm SHA256).Hash.ToLowerInvariant()
    if (Invoke-Native 'verify the zip layout and hashes' 'uv' @('run', '--frozen', 'python', 'scripts/verify_windows_zip.py', '--zip', $WindowsZip, '--expected-version', $VersionName, '--output', $out)) {
        $script:State.WindowsZipSha = $sha
        Save-State
        Write-Ok "zip_sha256=$sha (unsigned). Send it with the zip to testers."
    } else {
        Write-Bad 'Zip verification failed. Do not distribute it.'
    }
}

function Invoke-WindowsLeakRecord {
    $ok = Confirm-Yes 'Did the ADR-196 leak test pass on a real Windows PC with the kill switch ON (steps 1, 2, 4, 5, 9)?'
    if ($ok) { $script:State.WindowsLeak = 'pass' } else { $script:State.WindowsLeak = 'fail' }
    Save-State
    if ($ok) { Write-Ok 'Leak test PASS recorded.' } else { Write-Bad 'Leak test FAIL recorded. Do not send the Windows zip to testers.' }
}

function Invoke-Publish {
    if ($script:State.PhoneTest -ne 'pass') { Write-Bad 'The phone test is not recorded as PASS. Publishing is blocked (item 9).'; return }
    if (-not $script:State.ApiDeployed) { Write-Bad 'The API is not recorded as deployed. Publishing is blocked (item 7).'; return }
    if (-not (Test-Path -LiteralPath $ApkCopy)) { Write-Bad 'APK copy not found. Build it first (item 8).'; return }
    if (-not (Confirm-Typed 'PUBLISH' "Publish $VersionName as the in-app update. Testers on older builds get it. The script asks for the update signing passphrase and a final YES.")) { Write-Info 'Cancelled.'; return }
    try {
        & (Join-Path $root 'scripts\release_app_beta37.ps1') -PublishOnly
        $script:State.Published = (Get-Date).ToString('yyyy-MM-dd')
        Save-State
        Write-Ok 'Publish step finished. Check the update offer on a phone on the previous build.'
    } catch {
        Write-Bad ("Publish stopped: " + $_.Exception.Message + ' (nothing was uploaded unless you typed YES)')
    }
}

function Set-Settings {
    Write-Title 'Settings (Enter keeps the value in brackets)'
    $script:State.SourceRev = (Read-Host "Source commit [$($script:State.SourceRev)]") -replace '^\s*$', $script:State.SourceRev
    $script:State.NodeBaseline = (Read-Host "Node baseline commit [$($script:State.NodeBaseline)]") -replace '^\s*$', $script:State.NodeBaseline
    $script:State.ApiBaseline = (Read-Host "API baseline commit [$($script:State.ApiBaseline)]") -replace '^\s*$', $script:State.ApiBaseline
    $script:State.BeforeRevision = (Read-Host "Alembic revision the API database is at now [$($script:State.BeforeRevision)]") -replace '^\s*$', $script:State.BeforeRevision
    Write-Info 'API database: local = Docker database (scripts default); managed = managed PostgreSQL (ADR-197, needs a snapshot first).'
    $mode = (Read-Host "Database mode [local/managed] [$($script:State.Database)]").Trim()
    if ($mode -eq 'local' -or $mode -eq 'managed') { $script:State.Database = $mode }
    if ($script:State.Database -eq 'managed') {
        $script:State.SnapshotRef = (Read-Host "Snapshot id from the provider console [$($script:State.SnapshotRef)]").Trim()
    } else {
        $script:State.SnapshotRef = ''
    }
    Save-State
    Write-Ok 'Settings saved to the local state file.'
}

function Show-Menu {
    Write-Host ''
    Write-Host "DetourVpn $VersionName release console" -ForegroundColor Cyan
    Write-Host '  1   Status (read-only)'
    Write-Host '  2   Window tests, Gate 0 (disposable PostgreSQL, no beta access)'
    Write-Host '  3   Node dry runs, all three (read-only)'
    Write-Host '  4   Node baseline check (read-only, reads files on the nodes)'
    Write-Host '  5   Update the next node agent (dry run, then typed YES)'
    Write-Host '  6   API preflight (read-only)'
    Write-Host '  7   API deploy (typed YES, needs all nodes done)'
    Write-Host '  8   Android: build signed APK and install on the USB phone'
    Write-Host '  9   Phone test: record pass or fail'
    Write-Host '  10  Windows zip: build and verify (unsigned)'
    Write-Host '  11  Windows leak test: record result (ADR-196)'
    Write-Host '  12  Publish the in-app update (typed PUBLISH, needs phone PASS)'
    Write-Host '  S   Settings (commits, baselines, API database mode)'
    Write-Host '  0   Exit'
}

Load-State
Show-Status
if ($Status) { return }

while ($true) {
    Show-Menu
    $choice = (Read-Host 'Choose').Trim().ToUpperInvariant()
    switch ($choice) {
        '1'  { Show-Status }
        '2'  { Invoke-Gate0 }
        '3'  { Invoke-NodeDryRunAll }
        '4'  { Invoke-NodeBaselineCheck }
        '5'  { Invoke-UpdateNextNode }
        '6'  { Invoke-ApiPreflight }
        '7'  { Invoke-ApiDeploy }
        '8'  { Invoke-ApkStage1 }
        '9'  { Invoke-PhoneTest }
        '10' { Invoke-WindowsZip }
        '11' { Invoke-WindowsLeakRecord }
        '12' { Invoke-Publish }
        'S'  { Set-Settings }
        '0'  { return }
        default { Write-Bad 'Unknown choice.' }
    }
    Write-Host ''
    Write-Host "Next step: $(Get-NextStep)" -ForegroundColor Yellow
}
