[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$betaHost = '193.68.88.239'
$keyPath = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$helperPath = Join-Path (Split-Path -Parent $PSCommandPath) 'beta_store_bot_token.py'

if (-not $RunHere) {
    $pwshPath = (Get-Command pwsh.exe -ErrorAction Stop).Source
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $PSCommandPath
    Start-Process -FilePath $pwshPath -ArgumentList $arguments -WindowStyle Normal
    return
}

if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'Beta SSH identity missing.' }
$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$scpPath = (Get-Command scp.exe -ErrorAction Stop).Source
$expectedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $helperPath).Hash.ToLowerInvariant()
$runId = [guid]::NewGuid().ToString('N')
$remoteHelper = "/tmp/detourvpn-beta-token-$runId.py"
$remote = "root@$betaHost"
$sshOptions = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-i', $keyPath)
$staged = $false

try {
    & $sshPath @sshOptions $remote "test ! -e $remoteHelper && systemctl is-active --quiet detourvpn-beta-api.service"
    if ($LASTEXITCODE -ne 0) { throw 'Beta API preflight failed.' }
    & $scpPath @sshOptions $helperPath "${remote}:$remoteHelper"
    if ($LASTEXITCODE -ne 0) { throw 'Token helper transfer failed.' }
    $staged = $true
    $remoteHash = (& $sshPath @sshOptions $remote "sha256sum $remoteHelper" | ForEach-Object { ($_ -split '\s+')[0] })
    if ($remoteHash -ne $expectedHash) { throw 'Remote token helper hash mismatch.' }
    & $sshPath @sshOptions $remote "chmod 0600 -- $remoteHelper && cd /opt/detourvpn-beta && .venv/bin/python $remoteHelper preflight"
    if ($LASTEXITCODE -ne 0) { throw 'Beta token preflight failed; no token was requested.' }

    Write-Host 'Target: isolated beta API only. Auth, Mini App, invites, API ingress and service restart remain disabled.'
    Write-Host 'Enter the Bot API token for @uysdgyfudf_bot below. Input is hidden and never sent to chat.'
    $secureToken = Read-Host 'Bot API token (hidden)' -AsSecureString
    $bstr = [IntPtr]::Zero
    $plainToken = $null
    try {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
        $plainToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)

        $psi = [Diagnostics.ProcessStartInfo]::new()
        $psi.FileName = $sshPath
        $psi.UseShellExecute = $false
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        foreach ($argument in (@('-T') + $sshOptions + @($remote, "cd /opt/detourvpn-beta && .venv/bin/python $remoteHelper store"))) {
            [void]$psi.ArgumentList.Add($argument)
        }
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $psi
        if (-not $process.Start()) { throw 'Secure SSH token transfer did not start.' }
        $process.StandardInput.WriteLine($plainToken)
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($stdout) { Write-Host $stdout.TrimEnd() }
        if ($process.ExitCode -ne 0) {
            if ($stderr) { Write-Host 'Secure beta token storage failed; no secret details were captured.' }
            throw 'Beta token storage failed.'
        }
        Write-Host 'Token is stored in beta api.env; the running API was not restarted or activated.'
    } finally {
        $plainToken = $null
        if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if ($secureToken) { $secureToken.Dispose() }
        if ($process) { $process.Dispose() }
    }
} finally {
    if ($staged) { & $sshPath @sshOptions $remote "rm -f -- $remoteHelper" 2>$null }
}

Read-Host 'Press Enter to close this beta token setup window' | Out-Null
