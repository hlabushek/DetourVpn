[CmdletBinding()]
param(
    # Skip steps that were already done (for a re-run after a failure).
    [switch]$SkipNodes,
    [switch]$SkipApi,
    [switch]$SkipPool,
    # Skip the Windows zip build.
    [switch]$NoWindows,
    # Skip installing the APK on the USB-connected phone.
    [switch]$NoInstall,
    # Only run the read-only parts (checks and dry runs); change nothing on any server.
    [switch]$DryRunOnly,
    [string]$ApiOrigin = 'https://193.68.88.239'
)
# ONE command for the whole overnight rollout (ADR-096..116):
#   1. preflight: clean git tree, USB phone visible, SSH key usable
#   2. node agents (all nodes in the inventory)       -> update_beta_node_agent.py
#   3. API + migrations 0023..0027                    -> deploy_beta_api_latest.py
#   4. nodes switched to pool mode (disconnects users)-> convert_beta_node_pool.py
#   5. admin panel + Mini App + signed APK + install on the phone + Windows zip -> release_beta.ps1
# Every server tool is run as a dry run first and only then with --execute. It stops at the first
# failure; each tool rolls itself back. Passwords: the keystore passwords are typed into hidden
# prompts of the APK build. No secret is stored or printed by this script.
# NOT run end to end by its author: the first run is the first real test. Read it before running.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
function Step([string]$Title) { Write-Host ''; Write-Host "=== $Title ===" -ForegroundColor Cyan }
function Run([string]$Name, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "FAILED: $Name (exit code $LASTEXITCODE). Nothing after this step was run." }
}

Step 'Preflight'
$dirty = @(git status --porcelain | Where-Object { $_ -notmatch '^\?\? \.claude/' })
if ($dirty.Count -gt 0) { throw 'There are uncommitted changes. The deploy tools use committed code only; commit first.' }
Write-Host ('branch=' + (git rev-parse --abbrev-ref HEAD).Trim() + '; commit=' + (git rev-parse --short HEAD).Trim())
$adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
if (-not $NoInstall) {
    if (-not (Test-Path -LiteralPath $adb)) { throw 'adb not found. Install Android platform-tools or pass -NoInstall.' }
    $devices = @(& $adb devices | Select-Object -Skip 1 | Where-Object { $_ -match '\sdevice$' })
    if ($devices.Count -ne 1) { throw "Connect exactly one phone by USB with USB debugging on (found $($devices.Count)), or pass -NoInstall." }
    Write-Host 'phone=connected'
}
$nodes = @(uv run --frozen python -c "from scripts.update_beta_node_agent import DEFAULT_INVENTORY; from scripts.beta_nodes import load_inventory; print('\n'.join(load_inventory(DEFAULT_INVENTORY)))")
if ($LASTEXITCODE -ne 0 -or $nodes.Count -lt 1) { throw 'Could not read the node inventory (infra/beta/nodes.json).' }
Write-Host "nodes=$($nodes.Count) (names only; no addresses printed)"

Step 'Plan'
Write-Host '1. Update node agent on each node (agent restarts; connected users reconnect by themselves)'
Write-Host '2. Deploy API + migrations up to 0027 (DB dump first; automatic rollback)'
Write-Host '3. Convert each node to pool mode (DISCONNECTS that node''s users for a short time; automatic rollback)'
Write-Host '4. Deploy admin panel and Mini App, build the signed APK (hidden password prompts), install on the phone'
if (-not $NoWindows) { Write-Host '5. Build the unsigned Windows zip' }
Write-Host 'After this you still have to: open UDP 51899 inbound at each VPS provider; run the device checks.'
if ($DryRunOnly) { Write-Host 'DRY RUN ONLY: servers will not be changed.' -ForegroundColor Yellow }
else {
    $answer = Read-Host 'Type YES (capital letters) to run this on the beta servers'
    if ($answer -cne 'YES') { throw 'Cancelled. Nothing was changed.' }
}

function Server([string]$Name, [string[]]$ToolArgs) {
    # Dry run first, always.
    Run "$Name (dry run)" { uv run --frozen python @ToolArgs }
    if (-not $DryRunOnly) { Run "$Name (execute)" { uv run --frozen python @ToolArgs '--execute' } }
}

if (-not $SkipNodes) {
    foreach ($n in $nodes) {
        Step "Node agent: $n"
        Run "baseline $n" { uv run --frozen python -m scripts.update_beta_node_agent --node $n --check-baseline }
        if (-not $DryRunOnly) { Run "agent update $n" { uv run --frozen python -m scripts.update_beta_node_agent --node $n --execute } }
    }
}
if (-not $SkipApi) {
    Step 'API and migrations (0023..0027)'
    Server 'API deploy' @('scripts/deploy_beta_api_latest.py', '--source-rev', 'HEAD')
}
if (-not $SkipPool) {
    foreach ($n in $nodes) {
        Step "Pool mode: $n"
        if ($DryRunOnly) {
            # The pool dry run needs the new agent code and API on the host, so it cannot pass before steps 2-3 ran.
            Write-Host 'skipped in a dry run: needs the updated agent and API first (it is checked right before the real change)'
            continue
        }
        Server "pool $n" @('-m', 'scripts.convert_beta_node_pool', '--node', $n)
    }
}

if ($DryRunOnly) {
    Step 'Dry run finished'
    Write-Host 'Read-only parts finished. Run again without -DryRunOnly to do everything.'
    return
}

Step 'Admin panel, Mini App, signed APK, install, Windows zip'
$releaseArgs = @{ ApiOrigin = $ApiOrigin; WindowSeconds = 900 }
if (-not $NoInstall) { $releaseArgs.Install = $true }
if (-not $NoWindows) { $releaseArgs.Windows = $true }
& (Join-Path $root 'scripts\release_beta.ps1') @releaseArgs
if (-not $?) { throw 'FAILED: release_beta.ps1' }

Step 'Done'
Write-Host 'Still manual: UDP 51899 inbound at each provider; device checks (docs/runbooks/beta-fault-tolerance-rollout.md step 6).'
Write-Host 'Not done on purpose: in-app update signing key (docs/overnight/DEPLOY_RUNBOOK.md section 4); panel delivery stays off.'
