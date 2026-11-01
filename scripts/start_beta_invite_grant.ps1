[CmdletBinding()]
param(
    [switch]$RunHere,
    [ValidateSet('grant', 'revoke')]
    [string]$Operation = 'grant'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaOrigin = 'https://193.68.88.239'

if (-not $RunHere) {
    $pwshPath = (Get-Command pwsh.exe -ErrorAction Stop).Source
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere -Operation {1}' -f $PSCommandPath, $Operation
    Start-Process -FilePath $pwshPath -ArgumentList $arguments -WindowStyle Normal
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
$secureTelegramId = $null
$password = $null
$code = $null
$telegramIdText = $null
$telegramId = $null
$adminToken = $null

try {
    Write-Host "Target: isolated beta API only. Operation: $Operation. Do not paste credentials in chat."
    $secureTelegramId = Read-Host 'Tester Telegram numeric ID (hidden)' -AsSecureString
    $securePassword = Read-Host 'beta-owner admin password (hidden)' -AsSecureString
    $secureCode = Read-Host 'Current TOTP code (hidden)' -AsSecureString
    $telegramIdText = ConvertFrom-SecureInput $secureTelegramId
    if ($telegramIdText -notmatch '^[1-9][0-9]{0,14}$' -or
        -not [long]::TryParse($telegramIdText, [ref]$telegramId) -or
        $telegramId -ge [long]4503599627370496) {
        throw 'Tester Telegram ID is outside the supported range.'
    }
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

    $operationName = if ($Operation -eq 'grant') { 'grant_beta_invite' } else { 'revoke_beta_invite' }
    $reason = if ($Operation -eq 'grant') {
        'Owner-requested closed beta access for account login validation.'
    } else {
        'Owner-requested beta access removal after account login validation.'
    }
    $actionBody = @{
        operation = $operationName
        idempotency_key = [guid]::NewGuid().ToString()
        reason = $reason
        telegram_subject = $telegramId
    } | ConvertTo-Json -Compress
    $result = Invoke-RestMethod -Method Post -Uri "$betaOrigin/api/admin/v1/actions" `
        -Headers @{ Authorization = "Bearer $adminToken" } `
        -ContentType 'application/json' -Body $actionBody -TimeoutSec 20
    $expectedStatus = if ($Operation -eq 'grant') { 'active' } else { 'revoked' }
    if ($result.status -ne $expectedStatus) { throw "Beta invite status was not confirmed for $Operation." }
    Write-Host "beta_invite_status=$expectedStatus"
} catch {
    Write-Host "Beta invite $Operation was not confirmed. Check the non-secret error state in this window."
    if ($_.Exception.Response) {
        try {
            $status = [int]$_.Exception.Response.StatusCode
            Write-Host "http_status=$status"
        } catch { }
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
    $telegramIdText = $null
    $telegramId = $null
    $operationName = $null
    $reason = $null
    $actionBody = $null
    $login = $null
    $result = $null
    $expectedStatus = $null
    if ($secureTelegramId) { $secureTelegramId.Dispose() }
    if ($securePassword) { $securePassword.Dispose() }
    if ($secureCode) { $secureCode.Dispose() }
    $adminToken = $null
}
