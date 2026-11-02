[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaOrigin = 'https://193.68.88.239'
$expectedNodeName = 'detourvpn-beta-helsinki'

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
$failureStage = 'input'

try {
    Write-Host 'Read-only isolated beta node status. No VPN/account/ledger changes.'
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

    $failureStage = 'node_list'
    $nodes = @(Invoke-RestMethod -Method Get -Uri "$betaOrigin/api/admin/v1/nodes" `
        -Headers @{ Authorization = "Bearer $adminToken" } -TimeoutSec 20)
    while ($nodes.Count -eq 1 -and $nodes[0] -is [System.Array]) {
        $nodes = @($nodes[0])
    }
    $matches = @($nodes | Where-Object { $_.name -ceq $expectedNodeName })
    if ($matches.Count -ne 1) {
        Write-Host "beta_node_status=ambiguous; matching_nodes=$($matches.Count); no node identifiers shown"
        return
    }
    $node = $matches[0]
    $lastSeenPresent = $null -ne $node.last_seen_at
    Write-Host "beta_node_status=read_only; status=$($node.status); protocol=$($node.peer_protocol_version); occupied=$($node.occupied); unacknowledged=$($node.unacknowledged_commands); stalled=$($node.stalled_commands); last_seen_present=$($lastSeenPresent.ToString().ToLower())"
    Write-Host 'This reports node control-plane health only; it does not confirm a peer handshake or VPN egress.'
} catch {
    $failureType = $_.Exception.GetType().Name
    Write-Host "beta_node_status=not_confirmed; failure_stage=$failureStage; exception_type=$failureType; no secret or node identifier is shown"
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
