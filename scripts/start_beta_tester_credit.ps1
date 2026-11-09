[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaOrigin = 'https://193.68.88.239'
$creditBytes = [long]5368709120

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
    Write-Host 'Beta tester credit: exactly 5 GiB (ADR-090) to the newest account when it is active with zero granted quota; no payment.'
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
    $login = Invoke-RestMethod -Method Post -Uri "$betaOrigin/api/admin/v1/login" `
        -ContentType 'application/json' -Body $loginBody -TimeoutSec 60
    $adminToken = [string]$login.access_token
    if ([string]::IsNullOrWhiteSpace($adminToken) -or $login.role -ne 'admin') {
        throw 'Beta admin login did not return an administrator session.'
    }
    $headers = @{ Authorization = "Bearer $adminToken" }

    $users = @(Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users" `
        -Headers $headers -TimeoutSec 60)
    # Windows PowerShell 5.1 may deliver a JSON array as one nested item; flatten it.
    while ($users.Count -eq 1 -and $users[0] -is [System.Array]) { $users = @($users[0]) }
    $candidates = @()
    foreach ($item in $users) {
        $detail = Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users/$($item.id)" `
            -Headers $headers -TimeoutSec 60
        if ($detail.status -eq 'active' -and [long]$detail.quota.granted_bytes -eq 0 -and
            [long]$detail.quota.reserved_bytes -eq 0) { $candidates += $detail }
    }
    # The list is newest first: only the newest account overall may be credited, and it must have zero quota.
    if ($candidates.Count -lt 1 -or [string]$candidates[0].id -ne [string]$users[0].id) { throw "Refusing credit: the newest account is not an active zero-quota account (zero-quota active accounts: $($candidates.Count))." }
    $userId = [string]$candidates[0].id
    if ($userId -notmatch '^[0-9a-fA-F-]{36}$') { throw 'Account identifier is invalid.' }
    $idempotencyKey = [guid]::NewGuid().ToString()
    $actionBody = @{
        operation = 'credit'
        idempotency_key = $idempotencyKey
        reason = 'Closed beta tester starting allowance 5 GiB per ADR-090; no payment.'
        user_id = $userId
        amount_bytes = $creditBytes
    } | ConvertTo-Json -Compress
    $action = $null
    for ($attempt = 0; $attempt -lt 2 -and $null -eq $action; $attempt++) {
        try {
            $action = Invoke-RestMethod -Method Post -Uri "$betaOrigin/api/admin/v1/actions" `
                -Headers $headers -ContentType 'application/json' -Body $actionBody -TimeoutSec 60
        } catch {
            $apiError = [string]$_.ErrorDetails.Message
            if ($apiError.Length -gt 160) { $apiError = $apiError.Substring(0, 160) }
            Write-Host "credit_attempt_$attempt api_error=$apiError"
            if ($attempt -eq 1) { Write-Host 'Admin response was not received; checking the ledger before reporting.' }
        }
    }
    $after = (Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/users/$userId" `
        -Headers $headers -TimeoutSec 60).quota
    if ([long]$after.granted_bytes -ne $creditBytes -or [long]$after.reserved_bytes -ne 0) {
        throw 'The credit was not confirmed against the expected ledger totals.'
    }
    Write-Host 'beta_tester_credit=5368709120_bytes; reserved_bytes=0'
} catch {
    Write-Host 'Beta tester credit was not confirmed. No secret or account identifier is shown.'
    $dbg = [string]$_.Exception.Message; if ($dbg.Length -gt 200) { $dbg = $dbg.Substring(0, 200) }
    Write-Host "error_type=$($_.Exception.GetType().Name) line=$($_.InvocationInfo.ScriptLineNumber) message=$dbg"
    if ($_.Exception.Response) {
        try { Write-Host "http_status=$([int]$_.Exception.Response.StatusCode) error=$([string]$_.ErrorDetails.Message)" } catch { }
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
