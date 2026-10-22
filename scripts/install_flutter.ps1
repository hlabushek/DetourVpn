param([string]$ToolchainDirectory = "$env:LOCALAPPDATA\ProjectVpn\toolchains")
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPin = Get-Content -LiteralPath (Join-Path $taskRoot 'apps\client\toolchain.json') | ConvertFrom-Json
$taskSdk = Join-Path $ToolchainDirectory 'flutter'
if (Test-Path -LiteralPath (Join-Path $taskSdk 'bin\flutter.bat')) {
    $taskRevision = (& git -C $taskSdk rev-parse HEAD).Trim()
    if ($taskRevision -ne $taskPin.revision) { throw 'Existing Flutter revision differs; choose a separate toolchain directory.' }
} else {
    New-Item -ItemType Directory -Force -Path $ToolchainDirectory | Out-Null
    $taskArchive = Join-Path $ToolchainDirectory 'flutter.zip'
    & curl.exe -fL --retry 2 --output $taskArchive ('https://storage.googleapis.com/flutter_infra_release/' + $taskPin.archive)
    if ($LASTEXITCODE -ne 0) { throw 'Flutter download failed' }
    if ((Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskPin.sha256) {
        throw 'Flutter archive SHA256 mismatch'
    }
    & tar.exe -xf $taskArchive -C $ToolchainDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Flutter extraction failed' }
}
# Avoid unpacking every example package on first launch; pub get installs only locked dependencies.
$taskPreload = Join-Path $taskSdk '.pub-preload-cache'
if (Test-Path -LiteralPath $taskPreload) {
    $taskResolved = (Resolve-Path -LiteralPath $taskPreload).Path
    if ((Split-Path -Parent $taskResolved) -ne [IO.Path]::GetFullPath($taskSdk)) { throw 'Unexpected preload target' }
    Move-Item -LiteralPath $taskResolved -Destination (Join-Path $taskSdk '.pub-preload-cache-disabled')
}
& (Join-Path $taskSdk 'bin\flutter.bat') --disable-analytics
if ($LASTEXITCODE -ne 0) { throw 'Flutter initialization failed' }
Write-Output "Flutter $($taskPin.version) is ready at $taskSdk"
