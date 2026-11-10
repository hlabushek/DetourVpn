[CmdletBinding()]
param([switch]$RunHere, [int]$Seconds = 120)
# I-032 stage 1 gate in one step: self-elevates, uses the default build paths, asks for one test
# domain, then runs scripts/i032_engine_gate.py (one beta window; the owner initiates this Connect).
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if (-not $RunHere) {
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere -Seconds {1}' -f $PSCommandPath, $Seconds
    Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList $arguments -Verb RunAs
    return
}

$work = Join-Path $env:LOCALAPPDATA 'ProjectVpn'
$engine = Join-Path $work 'engine-windows\out\detour-engine.exe'
$tool = Join-Path $work 'cpp-tests\build\detour_config_tool.exe'
foreach ($path in @($engine, (Join-Path (Split-Path $engine) 'wintun.dll'), $tool)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing build output (run the build scripts first): $path" }
}
$domain = Read-Host 'Test domain to exclude (an IP-echo site, e.g. example.com)'
if (-not $domain) { throw 'A test domain is required.' }

Set-Location $repo
Write-Output "During the hold: open $domain (expect your home IP) and another IP-echo site (expect the VPN IP)."
Write-Output 'Meanwhile run scripts\check_beta_node_status.ps1 for the read-only peer status.'
& (Join-Path $repo '.venv\Scripts\python.exe') -m scripts.i032_engine_gate --execute `
    --engine $engine --tool $tool --mode exclude --domain "*.$domain" --domain $domain --seconds $Seconds
Write-Output 'Afterwards run scripts\check_beta_owner_window_status.ps1 (expect closed, 0, 0).'
