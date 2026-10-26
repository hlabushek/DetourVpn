[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaOrigin = 'https://193.68.88.239'
$testCreditBytes = [long]1048576

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

$securePassword = $null
$secureCode = $null
$password = $null
$code = $null
$adminToken = $null

try {
    Write-Host 'Beta-only one-time owner test credit: exactly 1 MiB. No payment or B/production changes.'
    Write-Host 'The operation proceeds only if the beta API lists exactly one active account with a zero ledger balance.'
    $securePassword = Read-Host 'beta-owner admin password (hidden)' -AsSecureString
    $secureCode = Read-Host 'Current TOTP code (hidden)' -AsSecureString
    if ($securePassword.Length -lt 16 -or $securePassword.Length -gt 256) {
        throw 'Admin password length is invalid.'
    }
    $password = ConvertFrom-SecureInput $securePassword
    $code = ConvertFrom-SecureInput $secureCode
    if ($code -notmatch '^[0-9]{6}$') { throw 'TOTP input must contain six digits.' }

    $loginBody = @{
        username = 'beta-owner'
        password = $password
        code = $code
    } | ConvertTo-Json -Compress
    $login = Invoke-RestMethod -Method Post -Uri "$betaOrigin/api/admin/v1/login" `
        -ContentType 'application/json' -Body $loginBody -TimeoutSec 20
    $adminToken = [string]$login.access_token
    if ([string]::IsNullOrWhiteSpace($adminToken) -or $login.role -ne 'admin') {
        throw 'Beta admin login did not return an administrator session.'
    }
    $headers = @{ Authorization = "Bearer $adminToken" }

    $users = @(Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users" `
        -Headers $headers -TimeoutSec 20)
    if ($users.Count -ne 1 -or $users[0].status -ne 'active') {
        throw 'Refusing credit: beta account is not uniquely identified and active.'
    }
    $userId = [string]$users[0].id
    if ($userId -notmatch '^[0-9a-fA-F-]{36}$') { throw 'Beta account identifier is invalid.' }
    $user = Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users/$userId" `
        -Headers $headers -TimeoutSec 20
    $quota = $user.quota
    if ($user.status -ne 'active' -or
        [long]$quota.granted_bytes -ne 0 -or
        [long]$quota.used_bytes -ne 0 -or
        [long]$quota.reserved_bytes -ne 0 -or
        [long]$quota.available_bytes -ne 0) {
        throw 'Refusing credit: owner beta ledger is not empty.'
    }

    $idempotencyKey = [guid]::NewGuid().ToString()
    $actionBody = @{
        operation = 'credit'
        idempotency_key = $idempotencyKey
        reason = 'Single owner-only 1 MiB allowance for the isolated beta node smoke test; no payment.'
        user_id = $userId
        amount_bytes = $testCreditBytes
    } | ConvertTo-Json -Compress
    $action = $null
    $actionResponseRecovered = $false
    for ($attempt = 0; $attempt -lt 2 -and $null -eq $action; $attempt++) {
        try {
            $action = Invoke-RestMethod -Method Post -Uri "$betaOrigin/api/admin/v1/actions" `
                -Headers $headers -ContentType 'application/json' -Body $actionBody -TimeoutSec 20
        } catch {
            if ($attempt -eq 1) { $actionResponseRecovered = $true }
        }
    }

    $verified = Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users/$userId" `
        -Headers $headers -TimeoutSec 20
    if ([long]$verified.quota.granted_bytes -ne $testCreditBytes -or
        [long]$verified.quota.available_bytes -ne $testCreditBytes -or
        [long]$verified.quota.used_bytes -ne 0 -or
        [long]$verified.quota.reserved_bytes -ne 0) {
        throw 'Beta credit was applied but the expected one-time balance did not verify.'
    }
    $recovered = if ($actionResponseRecovered) { 'true' } else { 'false' }
    Write-Host "beta_owner_test_credit=1048576_bytes; available=1048576_bytes; account_count=1; action_response_recovered=$recovered"
} catch {
    Write-Host 'Beta owner test credit was not confirmed. No secrets or account identifiers are shown.'
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
    $password = $null
    $code = $null
    $adminToken = $null
    if ($securePassword) { $securePassword.Dispose() }
    if ($secureCode) { $secureCode.Dispose() }
}
