[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [long]$TelegramId,
    [switch]$RunHere
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaOrigin = 'https://193.68.88.239'

if ($TelegramId -lt 1 -or $TelegramId -ge [long]4503599627370496) {
    throw 'Telegram ID is outside the supported range.'
}

if (-not $RunHere) {
    $pwshPath = (Get-Command pwsh.exe -ErrorAction Stop).Source
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -TelegramId {1} -RunHere' -f $PSCommandPath, $TelegramId
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
$password = $null
$code = $null
$adminToken = $null

try {
    Write-Host 'Target: isolated beta API only. Do not paste credentials in chat.'
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

    $actionBody = @{
        operation = 'grant_beta_invite'
        idempotency_key = [guid]::NewGuid().ToString()
        reason = 'Owner-requested closed beta access for account login validation.'
        telegram_subject = $TelegramId
    } | ConvertTo-Json -Compress
    $result = Invoke-RestMethod -Method Post -Uri "$betaOrigin/api/admin/v1/actions" `
        -Headers @{ Authorization = "Bearer $adminToken" } `
        -ContentType 'application/json' -Body $actionBody -TimeoutSec 20
    if ($result.status -ne 'active') { throw 'Beta invite did not become active.' }
    Write-Host "beta_invite_status=active; telegram_id=$TelegramId"
} catch {
    Write-Host 'Beta invite was not confirmed. Check the non-secret error state in this window.'
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
    if ($securePassword) { $securePassword.Dispose() }
    if ($secureCode) { $secureCode.Dispose() }
    $adminToken = $null
}
