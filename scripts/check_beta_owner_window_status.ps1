[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaOrigin = 'https://193.68.88.239'

if (-not $RunHere) {
    $windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $PSCommandPath
    Start-Process -FilePath $windowsPowerShell -ArgumentList $arguments -WindowStyle Normal
    return
}

function ConvertFrom-SecureInput([Security.SecureString]$Value) {
    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}

$secureOwnerId = $null
$securePassword = $null
$secureCode = $null
$ownerIdText = $null
$ownerId = 0L
$password = $null
$code = $null
$adminToken = $null
$failureStage = 'input'

try {
    Write-Host 'Read-only beta v3 peer status and byte reservation check.'
    Write-Host 'Only state, non-closed peer count and reserved bytes are displayed.'
    Write-Host 'Owner ID, password and TOTP stay in this local window.'
    $secureOwnerId = Read-Host 'Owner Telegram numeric ID (hidden)' -AsSecureString
    $securePassword = Read-Host 'beta-owner admin password (hidden)' -AsSecureString
    $secureCode = Read-Host 'Current TOTP code (hidden)' -AsSecureString
    $ownerIdText = ConvertFrom-SecureInput $secureOwnerId
    if ($ownerIdText -notmatch '^[1-9][0-9]{0,14}$' -or
        -not [long]::TryParse($ownerIdText, [ref]$ownerId) -or
        $ownerId -ge [long]4503599627370496) {
        throw 'Owner identifier is outside the supported numeric range.'
    }
    if ($securePassword.Length -lt 16 -or $securePassword.Length -gt 256) {
        throw 'Admin password length is invalid.'
    }
    $password = ConvertFrom-SecureInput $securePassword
    $code = ConvertFrom-SecureInput $secureCode
    if ($code -notmatch '^[0-9]{6}$') { throw 'TOTP input must contain six digits.' }

    $loginBody = @{ username = 'beta-owner'; password = $password; code = $code } |
        ConvertTo-Json -Compress
    $failureStage = 'admin_login'
    $login = Invoke-RestMethod -Method Post -Uri "$betaOrigin/api/admin/v1/login" `
        -ContentType 'application/json' -Body $loginBody -TimeoutSec 20
    $adminToken = [string]$login.access_token
    if ([string]::IsNullOrWhiteSpace($adminToken) -or $login.role -ne 'admin') {
        throw 'Beta admin login did not return an administrator session.'
    }
    $headers = @{ Authorization = "Bearer $adminToken" }

    $failureStage = 'owner_lookup'
    $users = @(Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users?q=$ownerIdText" `
        -Headers $headers -TimeoutSec 20)
    while ($users.Count -eq 1 -and $users[0] -is [System.Array]) {
        $users = @($users[0])
    }
    if ($users.Count -ne 1 -or $users[0].status -ne 'active') {
        throw 'Owner did not resolve to exactly one active beta account.'
    }
    $userId = [string]$users[0].id
    if ($userId -notmatch '^[0-9a-fA-F-]{36}$') { throw 'Owner account identifier is invalid.' }

    $failureStage = 'window_status_read'
    $status = Invoke-RestMethod -Method Get `
        -Uri "$betaOrigin/api/admin/v1/users/$userId/window-status" `
        -Headers $headers -TimeoutSec 20
    $state = if ($null -eq $status.latest_connection_state) { 'none' } else { [string]$status.latest_connection_state }
    $nonClosed = [long]$status.non_closed_connection_count
    $reserved = [long]$status.reserved_bytes
    if ($nonClosed -lt 0 -or $reserved -lt 0 -or
        $state -notin @('none', 'pending', 'active', 'revoke_pending', 'closed')) {
        throw 'Read-only window status response did not match the reviewed shape.'
    }
    $gate = if ($state -eq 'closed' -and $nonClosed -eq 0 -and $reserved -eq 0) { 'closed' } else { 'open' }
    Write-Host "beta_owner_window_status=read_only; latest_state=$state; non_closed_connections=$nonClosed; reserved_bytes=$reserved; revoke_gate=$gate"
} catch {
    $failureType = $_.Exception.GetType().Name
    Write-Host "beta_owner_window_status=not_confirmed; failure_stage=$failureStage; exception_type=$failureType; no secret or account identifier is shown"
    if ($_.Exception.Response) {
        try { Write-Host "http_status=$([int]$_.Exception.Response.StatusCode)" } catch { }
    }
    exit 1
} finally {
    if ($adminToken) {
        try {
            Invoke-RestMethod -Method Post -Uri "$betaOrigin/api/admin/v1/logout" `
                -Headers @{ Authorization = "Bearer $adminToken" } `
                -ContentType 'application/json' -Body '{}' -TimeoutSec 10 | Out-Null
        } catch { }
    }
    $ownerIdText = $null
    $ownerId = 0L
    $password = $null
    $code = $null
    $adminToken = $null
    if ($secureOwnerId) { $secureOwnerId.Dispose() }
    if ($securePassword) { $securePassword.Dispose() }
    if ($secureCode) { $secureCode.Dispose() }
}
