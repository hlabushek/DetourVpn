[CmdletBinding()]
param(
    # Optional authenticated aggregate read only; helper never submits a credit.
    [switch]$VerifyQuota,
    # Read only ADB transport observation; does not require VPN to be off.
    [switch]$ConnectivityOnly
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$appId = 'com.detourvpn.client'
$expectedVersionName = '0.1.0-beta.1'
$expectedVersionCode = 2
$expectedBaseApkSha256 = 'b6c1d1988279c5ca02b46fb7952f58f84870152d7fe18f912410264618bca746'
$adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'

function Stop-Preflight([string]$Reason) {
    Write-Host "i030_owner_preflight=blocked; reason=$Reason; no tunnel started"
    exit 2
}

if (-not (Test-Path -LiteralPath $adb -PathType Leaf)) {
    Stop-Preflight 'adb_missing'
}

$deviceOutput = @(& $adb devices 2>$null)
if ($LASTEXITCODE -ne 0) { Stop-Preflight 'adb_unavailable' }
$authorized = @(
    $deviceOutput | Where-Object { $_ -match '^\S+\s+device(?:\s|$)' }
)
if ($authorized.Count -ne 1) {
    Stop-Preflight 'expected_one_authorized_device'
}
$deviceSerial = (($authorized[0] -split '\s+')[0])

if ($ConnectivityOnly) {
    if ($VerifyQuota) { Stop-Preflight 'connectivity_only_cannot_query_quota' }
    $connectivityLines = @(& $adb -s $deviceSerial shell dumpsys connectivity 2>$null)
    if ($LASTEXITCODE -ne 0) { Stop-Preflight 'connectivity_query_failed' }
    $connectivity = $connectivityLines -join "`n"
    $vpnActive = [regex]::IsMatch($connectivity, '(?im)^\s*Transports:.*\bVPN\b')
    Write-Host "i030_adb_observation=pass; authorized_devices=1; active_vpn_transport=$($vpnActive.ToString().ToLower()); no_raw_dumpsys_saved=true"
    exit 0
}

$modelLines = @(& $adb -s $deviceSerial shell getprop ro.product.model 2>$null)
if ($LASTEXITCODE -ne 0) { Stop-Preflight 'device_query_failed' }
$model = ($modelLines -join '').Trim()
if ($model -ne 'Pixel 8 Pro') { Stop-Preflight 'unexpected_device_model' }

$packageLines = @(& $adb -s $deviceSerial shell dumpsys package $appId 2>$null)
if ($LASTEXITCODE -ne 0) { Stop-Preflight 'package_query_failed' }
$packageInfo = $packageLines -join "`n"
$versionNameMatch = [regex]::Match($packageInfo, '(?m)^\s*versionName=([^\s]+)')
$versionCodeMatch = [regex]::Match($packageInfo, '(?m)^\s*versionCode=(\d+)')
if (-not $versionNameMatch.Success -or -not $versionCodeMatch.Success) {
    Stop-Preflight 'target_app_missing_or_unreadable'
}
$versionName = $versionNameMatch.Groups[1].Value
$versionCode = [int]$versionCodeMatch.Groups[1].Value
if ($versionName -ne $expectedVersionName -or $versionCode -ne $expectedVersionCode) {
    Stop-Preflight 'app_version_does_not_match_reviewed_receipt'
}

$pathLines = @(& $adb -s $deviceSerial shell pm path $appId 2>$null)
if ($LASTEXITCODE -ne 0) { Stop-Preflight 'apk_path_query_failed' }
$basePathMatch = [regex]::Match(($pathLines -join "`n"), '(?m)^package:(\S+/base\.apk)\s*$')
if (-not $basePathMatch.Success) { Stop-Preflight 'base_apk_path_unavailable' }

$tempApk = Join-Path ([IO.Path]::GetTempPath()) ('i030-owner-base-' + [guid]::NewGuid().ToString('N') + '.apk')
try {
    $null = & $adb -s $deviceSerial pull $basePathMatch.Groups[1].Value $tempApk 2>$null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $tempApk -PathType Leaf)) {
        Stop-Preflight 'base_apk_read_failed'
    }
    $stream = [IO.File]::OpenRead($tempApk)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $installedHash = [Convert]::ToHexString($sha.ComputeHash($stream)).ToLowerInvariant() }
        finally { $sha.Dispose() }
    } finally { $stream.Dispose() }
} finally {
    if (Test-Path -LiteralPath $tempApk -PathType Leaf) {
        Remove-Item -LiteralPath $tempApk -Force
    }
}
if ($installedHash -ne $expectedBaseApkSha256) {
    Stop-Preflight 'installed_apk_does_not_match_signed_receipt'
}

$connectivityLines = @(& $adb -s $deviceSerial shell dumpsys connectivity 2>$null)
if ($LASTEXITCODE -ne 0) { Stop-Preflight 'connectivity_query_failed' }
$connectivity = $connectivityLines -join "`n"
$vpnActive = [regex]::IsMatch($connectivity, '(?im)^\s*Transports:.*\bVPN\b')
if ($vpnActive) { Stop-Preflight 'vpn_already_active_disconnect_before_test' }

Write-Host "i030_owner_preflight=pass; authorized_devices=1; device_model=Pixel 8 Pro; app=$appId; version=$versionName; version_code=$versionCode; signed_base_apk_match=true; active_vpn_transport=false; vpn_started_by_script=false"

if ($VerifyQuota) {
    $quotaHelper = Join-Path $PSScriptRoot 'start_beta_owner_acceptance_credit.ps1'
    if (-not (Test-Path -LiteralPath $quotaHelper -PathType Leaf)) {
        Stop-Preflight 'readonly_quota_helper_missing'
    }
    & $quotaHelper -RunHere -VerifyOnly
    if (-not $?) { Stop-Preflight 'readonly_quota_check_failed' }
}
