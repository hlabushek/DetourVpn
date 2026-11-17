# Deploy plan for the 3600 s access window (ADR-147, Phase 3.1).
#
# Order is fixed: node agents first, then the API, then the client build (see runbook
# docs/runbooks/deploy-window-3600.md). This script is a dry run by default. It prints each
# command and runs nothing that changes a server. Real changes need -Execute, and every
# deploy step then waits for you to type YES.
#
# Read-only steps (run always): the local pytest gate and the dry-run previews.
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File .\scripts\deploy_window_3600.ps1 `
#       -SourceRev <commit> -BaselineRev <revision the beta hosts run now> `
#       -BeforeRevision <alembic revision the API database is at now>
#   add -Execute only after reviewing the dry-run output.

param(
    [Parameter(Mandatory = $true)][string]$SourceRev,
    [Parameter(Mandatory = $true)][string]$BaselineRev,
    [Parameter(Mandatory = $true)][string]$BeforeRevision,
    [string[]]$Nodes = @('detourvpn-beta-helsinki', 'detourvpn-beta-germany', 'detourvpn-beta-netherlands'),
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$env:PYTHONPATH = $root

function Confirm-Step([string]$Text) {
    if (-not $Execute) { return $false }
    $answer = Read-Host "$Text  Type YES to run, anything else to stop"
    if ($answer -ne 'YES') { Write-Host 'Stopped by you. Nothing more was run.' -ForegroundColor Yellow; exit 0 }
    return $true
}

function Invoke-Checked([string[]]$Command) {
    Write-Host ('  > ' + ($Command -join ' ')) -ForegroundColor Cyan
    & $Command[0] $Command[1..($Command.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
        Write-Host "STOPPED: command failed with exit $LASTEXITCODE. Nothing after it was run." -ForegroundColor Red
        exit 1
    }
}

Write-Host 'Gate 0: local pytest for the window logic (no server access)'
$tests = @(
    'services/api/tests/test_window_service.py',
    'services/api/tests/test_window_runtime.py',
    'services/api/tests/test_window_pool.py',
    'services/api/tests/test_window_daemon.py',
    'services/api/tests/test_window_funding.py',
    'services/api/tests/test_peer_windows.py'
)
Invoke-Checked (@('uv', 'run', '--frozen', '--python', '3.13', 'pytest') + $tests + @('-q', '-p', 'no:cacheprovider'))

Write-Host ''
Write-Host 'Step 1: node agents (one at a time; the first node is the canary)'
foreach ($node in $Nodes) {
    Write-Host "Node $node, dry run (prints the plan, changes nothing):"
    Invoke-Checked @('python', '-m', 'scripts.update_beta_node_agent', '--node', $node, '--source-rev', $SourceRev, '--baseline-rev', $BaselineRev)
}
foreach ($node in $Nodes) {
    if (Confirm-Step "Update node agent on $node. This restarts the agent and drops its current connections.") {
        Invoke-Checked @('python', '-m', 'scripts.update_beta_node_agent', '--node', $node, '--source-rev', $SourceRev, '--baseline-rev', $BaselineRev, '--execute')
    }
}

Write-Host ''
Write-Host 'Step 2: API (after every node is updated)'
Write-Host 'Preflight (no network, prints the plan):'
Invoke-Checked @('python', 'scripts/deploy_beta_api_latest.py', '--source-rev', $SourceRev, '--baseline-rev', $BaselineRev, '--before-revision', $BeforeRevision)
if (Confirm-Step 'Deploy the API to the beta host (automatic rollback on failure).') {
    Invoke-Checked @('python', 'scripts/deploy_beta_api_latest.py', '--source-rev', $SourceRev, '--baseline-rev', $BaselineRev, '--before-revision', $BeforeRevision, '--execute')
}

Write-Host ''
Write-Host 'Step 3: client build (not run by this script; see the runbook, section Client)'
Write-Host 'Do not build the client with -WindowSeconds 3600 until the client bounds are changed and tested.'
Write-Host ''
Write-Host 'Done. Check the nodes and API as described in the runbook before you move on.' -ForegroundColor Green
