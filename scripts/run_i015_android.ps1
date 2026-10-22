$ErrorActionPreference = 'Stop'
if ($env:VPN_I013_STAGING_APPROVED -or $env:VPN_I014_LOCAL_ANDROID -eq '1') {
    throw 'I-015 is local only; clear staging and I-014 opt-ins first.'
}
$taskPreviousRewards = $env:VPN_I015_LOCAL_ANDROID
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskAdb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
$taskDevices = @(& $taskAdb devices | Where-Object { $_ -match '\tdevice\s*$' })
if ($taskDevices.Count -ne 1) { throw 'Connect/unlock exactly one authorized Android phone.' }
$taskOrdinaryApk = Join-Path $taskRoot 'apps\client\build\app\outputs\flutter-apk\app-debug.apk'
$taskBackupDirectory = Join-Path $taskRoot ('.cache\i-015\ordinary-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $taskBackupDirectory | Out-Null
$taskBackup = Join-Path $taskBackupDirectory 'app-debug.apk'
# Invoke only after the normal main.dart build; preserve it before test compilation.
Copy-Item -LiteralPath $taskOrdinaryApk -Destination $taskBackup
$taskHash = (Get-FileHash -LiteralPath $taskBackup -Algorithm SHA256).Hash
try {
    $env:VPN_I015_LOCAL_ANDROID = '1'
    & (Join-Path $PSScriptRoot 'run_i013_android.ps1')
} finally {
    $env:VPN_I015_LOCAL_ANDROID = $taskPreviousRewards
    $taskRestored = $false
    try {
        if ((Get-FileHash -LiteralPath $taskBackup -Algorithm SHA256).Hash -ne $taskHash) {
            throw 'Normal APK backup changed; cannot restore.'
        }
        Copy-Item -LiteralPath $taskBackup -Destination $taskOrdinaryApk -Force
        & $taskAdb install -r $taskBackup
        if ($LASTEXITCODE -ne 0) { throw 'Restore ordinary main.dart APK failed.' }
        & $taskAdb shell am force-stop org.example.vpnclient
        if ($LASTEXITCODE -ne 0) { throw 'Stopping restored app failed.' }
        $taskRestored = $true
    } finally {
        $taskEvidence = Join-Path $taskRoot 'docs\evidence\i-015-local-android.json'
        if (Test-Path -LiteralPath $taskEvidence) {
            $taskReceipt = Get-Content -LiteralPath $taskEvidence -Raw | ConvertFrom-Json
            $taskReceipt | Add-Member -NotePropertyName ordinary_apk_restored -NotePropertyValue $taskRestored -Force
            $taskReceipt.passed = $taskReceipt.passed -and $taskRestored
            $taskReceipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $taskEvidence -Encoding utf8
        }
    }
}
