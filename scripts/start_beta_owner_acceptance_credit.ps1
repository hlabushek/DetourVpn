[CmdletBinding()]
param(
    [switch]$RunHere,
    # Authenticated aggregate quota read only; never submits a credit action.
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaOrigin = 'https://193.68.88.239'
$previousGrantBytes = [long]2097152
$acceptanceCreditBytes = [long]4194304

if (-not $RunHere) {
    $windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $PSCommandPath
    if ($VerifyOnly) { $arguments += ' -VerifyOnly' }
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
$failureStage = 'input'

try {
    if ($VerifyOnly) {
        Write-Host 'Read-only beta owner quota check: no credit or ledger changes.'
    } else {
        Write-Host 'Beta-only owner acceptance credit: exactly 4 MiB; no payment.'
        Write-Host 'Requires one active beta account, exactly 2 MiB previously granted, and zero reserved bytes.'
    }
    Write-Host 'B/production are not contacted. Password and TOTP stay in this local window.'
    $securePassword = Read-Host 'beta-owner admin password (hidden)' -AsSecureString
    $secureCode = Read-Host 'Current TOTP code (hidden)' -AsSecureString
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

    $failureStage = 'account_list'
    $users = @(Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users" `
        -Headers $headers -TimeoutSec 20)
    if ($users.Count -ne 1 -or $users[0].status -ne 'active') {
        if ($VerifyOnly) {
            Write-Host "beta_owner_quota_status=ambiguous; active_account_count=$($users.Count); no identifiers shown"
            return
        }
        throw 'Refusing acceptance credit: beta account is not uniquely identified and active.'
    }
    $userId = [string]$users[0].id
    if ($userId -notmatch '^[0-9a-fA-F-]{36}$') { throw 'Beta account identifier is invalid.' }
    $failureStage = 'quota_read'
    $user = Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users/$userId" `
        -Headers $headers -TimeoutSec 20
    $before = $user.quota

    if ($VerifyOnly) {
        Write-Host ("beta_owner_quota_status=read_only; active_account_count=1; granted_bytes={0}; used_bytes={1}; reserved_bytes={2}; available_bytes={3}" -f `
            [long]$before.granted_bytes, [long]$before.used_bytes, `
            [long]$before.reserved_bytes, [long]$before.available_bytes)
        return
    }

    $failureStage = 'credit_precondition'
    if ($user.status -ne 'active' -or
        [long]$before.granted_bytes -ne $previousGrantBytes -or
        [long]$before.used_bytes -le 0 -or
        [long]$before.used_bytes -ge $previousGrantBytes -or
        [long]$before.reserved_bytes -ne 0 -or
        [long]$before.available_bytes -ne ($previousGrantBytes - [long]$before.used_bytes)) {
        throw 'Refusing acceptance credit: beta ledger does not match the expected closed 2 MiB test grant.'
    }

    $idempotencyKey = [guid]::NewGuid().ToString()
    $actionBody = @{
        operation = 'credit'
        idempotency_key = $idempotencyKey
        reason = 'One bounded 4 MiB owner-only allowance for remaining isolated beta Android acceptance checks; no payment.'
        user_id = $userId
        amount_bytes = $acceptanceCreditBytes
    } | ConvertTo-Json -Compress
    $action = $null
    $failureStage = 'credit_action'
    for ($attempt = 0; $attempt -lt 2 -and $null -eq $action; $attempt++) {
        try {
            $action = Invoke-RestMethod -Method Post -Uri "$betaOrigin/api/admin/v1/actions" `
                -Headers $headers -ContentType 'application/json' -Body $actionBody -TimeoutSec 20
        } catch {
            if ($attempt -eq 1) { Write-Host 'Admin response was not received; checking the ledger before reporting.' }
        }
    }

    $failureStage = 'credit_verification'
    $verifiedUser = Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users/$userId" `
        -Headers $headers -TimeoutSec 20
    $after = $verifiedUser.quota
    if ([long]$after.granted_bytes -ne ($previousGrantBytes + $acceptanceCreditBytes) -or
        [long]$after.used_bytes -ne [long]$before.used_bytes -or
        [long]$after.reserved_bytes -ne 0 -or
        [long]$after.available_bytes -ne ([long]$before.available_bytes + $acceptanceCreditBytes)) {
        throw 'Acceptance credit was not confirmed against the expected ledger totals.'
    }
    Write-Host 'beta_owner_acceptance_credit=4194304_bytes; granted_total=6291456_bytes; account_count=1; reserved_bytes=0'
} catch {
    Write-Host "beta_owner_acceptance_credit=not_confirmed; failure_stage=$failureStage; no secret or account identifier is shown"
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
