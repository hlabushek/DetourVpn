[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Build', 'Publish')]
    [string]$Stage,
    # Beta API origin, for example https://host. Needed for Build.
    [string]$ApiOrigin = '',
    # Lowest version code that may still run. 1 = nobody is forced to update.
    [int]$MinSupportedVersionCode = 1,
    [switch]$NoInstall
)
# Client release in two steps (ADR-107). Only chains the existing gated scripts.
#   Build:   .\scripts\release.ps1 -Stage Build -ApiOrigin https://<host>   (then test on the phone)
#   Publish: .\scripts\release.ps1 -Stage Publish                         (asks you to type YES)
# Not automated on purpose: git commit/push, backend deploy, migrations 0028 and 0029, node rollout.

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root

$dirty = @(git status --porcelain | Where-Object { $_ -notmatch '^\?\? (\.claude/|docs/evidence/|docs/research/)' })
if ($dirty.Count -gt 0) { throw 'Stop: uncommitted changes. Commit them first, so the build matches git.' }

if ($Stage -eq 'Build') {
    if ($ApiOrigin -notmatch '^https://[^\s/?#]+$') { throw 'Pass -ApiOrigin https://<host> (HTTPS origin, no path).' }
    & (Join-Path $root 'scripts\build_beta_apk.ps1') -ApiOrigin $ApiOrigin -WindowSeconds 900
    if ($LASTEXITCODE -ne 0) { throw 'FAILED: build_beta_apk.ps1' }
    $apk = Join-Path $root 'apps\client\build\app\outputs\flutter-apk\app-release.apk'
    if (-not $NoInstall) {
        $adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
        & $adb install -r $apk
        if ($LASTEXITCODE -ne 0) { throw 'FAILED: adb install' }
    }
    Write-Host "Built $apk. Test on the phone, then run: .\scripts\release.ps1 -Stage Publish"
    return
}

& (Join-Path $root 'scripts\enable_app_update.ps1') -Publish -MinSupportedVersionCode $MinSupportedVersionCode
if ($LASTEXITCODE -ne 0) { throw 'FAILED: publish' }
