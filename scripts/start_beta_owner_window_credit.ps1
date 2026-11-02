[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaOrigin = 'https://193.68.88.239'
$expectedGrantedBytes = [long]6291456
$expectedUsedBytes = [long]5416782
$expectedReservedBytes = [long]0
$expectedAvailableBytes = [long]874674
$windowCreditBytes = [long]262144

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
    Write-Host 'Beta-only one-time owner window credit: exactly 256 KiB; no payment.'
    Write-Host 'Requires the exact current owner ledger snapshot and zero reserved bytes.'
    Write-Host 'B/production are not contacted. ID, password and TOTP stay in this local window.'
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
        throw 'Owner ID did not resolve to exactly one active beta account.'
    }
    $userId = [string]$users[0].id
    if ($userId -notmatch '^[0-9a-fA-F-]{36}$') {
        throw 'Resolved owner account identifier has an unexpected format.'
    }

    $failureStage = 'ledger_preflight'
    $user = Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users/$userId" `
        -Headers $headers -TimeoutSec 20
    $before = $user.quota
    if ($user.status -ne 'active' -or
        [long]$before.granted_bytes -ne $expectedGrantedBytes -or
        [long]$before.used_bytes -ne $expectedUsedBytes -or
        [long]$before.reserved_bytes -ne $expectedReservedBytes -or
        [long]$before.available_bytes -ne $expectedAvailableBytes -or
        ([long]$before.granted_bytes - [long]$before.used_bytes -ne [long]$before.available_bytes)) {
        throw 'Owner quota changed from the reviewed snapshot; refusing additional credit.'
    }

    $idempotencyKey = [guid]::NewGuid().ToString()
    $actionBody = @{
        operation = 'credit'
        idempotency_key = $idempotencyKey
        reason = 'One bounded 256 KiB owner-only beta allowance to run a single 1 MiB window; no payment.'
        user_id = $userId
        amount_bytes = $windowCreditBytes
    } | ConvertTo-Json -Compress
    $action = $null
    $failureStage = 'credit_action'
    for ($attempt = 0; $attempt -lt 2 -and $null -eq $action; $attempt++) {
        try {
            $action = Invoke-RestMethod -Method Post -Uri "$betaOrigin/api/admin/v1/actions" `
                -Headers $headers -ContentType 'application/json' -Body $actionBody -TimeoutSec 20
        } catch {
            if ($attempt -eq 1) { Write-Host 'Admin response was not received; verifying the ledger with the same operation key.' }
        }
    }

    $failureStage = 'credit_verification'
    $verifiedUser = Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users/$userId" `
        -Headers $headers -TimeoutSec 20
    $after = $verifiedUser.quota
    $expectedGrantedAfter = $expectedGrantedBytes + $windowCreditBytes
    $expectedAvailableAfter = $expectedAvailableBytes + $windowCreditBytes
    if ([long]$after.granted_bytes -ne $expectedGrantedAfter -or
        [long]$after.used_bytes -ne $expectedUsedBytes -or
        [long]$after.reserved_bytes -ne 0 -or
        [long]$after.available_bytes -ne $expectedAvailableAfter) {
        throw 'Owner window credit was not confirmed against exact post-action ledger totals.'
    }
    Write-Host 'beta_owner_window_credit=262144_bytes; granted_total=6553600_bytes; used_bytes=5416782; reserved_bytes=0; available_bytes=1136818'
} catch {
    $failureType = $_.Exception.GetType().Name
    Write-Host "beta_owner_window_credit=not_confirmed; failure_stage=$failureStage; exception_type=$failureType; no secret or account identifier is shown"
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
