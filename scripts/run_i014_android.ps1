$ErrorActionPreference = 'Stop'
if ($env:VPN_I013_STAGING_APPROVED) { throw 'I-014 test is local only; clear staging opt-in first.' }
$taskPreviousQuota = $env:VPN_I014_LOCAL_ANDROID
try {
    $env:VPN_I014_LOCAL_ANDROID = '1'
    & (Join-Path $PSScriptRoot 'run_i013_android.ps1')
} finally { $env:VPN_I014_LOCAL_ANDROID = $taskPreviousQuota }
