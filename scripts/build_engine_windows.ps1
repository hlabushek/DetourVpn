[CmdletBinding()]
param(
    # Build detour-engine.exe (Amnezia Box, windows/amd64) from the pinned source archive.
    [switch]$Build,
    # Separate, explicit network step: download the pinned wintun zip and verify it. Needs owner approval.
    [switch]$FetchWintun,
    [string]$GoRoot = (Join-Path $PSScriptRoot '..\.cache\toolchains\go1.25.5\go'),
    [string]$GoPath = (Join-Path $PSScriptRoot '..\.cache\toolchains\go1.25.5\gopath'),
    [string]$WorkRoot = (Join-Path $env:LOCALAPPDATA 'ProjectVpn\engine-windows'),
    # Set to 'off' to refuse any module download (offline reproduction check).
    [string]$GoProxy = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not ($Build -or $FetchWintun)) { throw 'Pass -Build and/or -FetchWintun.' }

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$lockPath = Join-Path $repoRoot 'third_party\amnezia-box\source-lock.json'
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
$sourceArchive = Join-Path $repoRoot ('third_party\amnezia-box\' + $lock.archive)

# Build outside OneDrive: the work root must live under %LOCALAPPDATA%.
$localRoot = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd('\') + '\'
$workFull = [System.IO.Path]::GetFullPath($WorkRoot)
if (-not $workFull.StartsWith($localRoot, [System.StringComparison]::OrdinalIgnoreCase) -or $workFull -match 'OneDrive') {
    throw 'The work root must be under %LOCALAPPDATA% and outside OneDrive.'
}
$srcParent = Join-Path $workFull 'src'
$srcRoot = Join-Path $srcParent 'amnezia-box-1.260910.0'
$outDir = Join-Path $workFull 'out'
$goCache = Join-Path $workFull 'gocache'
$wintunDir = Join-Path $workFull 'wintun'

function Assert-Sha256([string]$Path, [string]$Expected, [string]$What) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) { throw "$What checksum mismatch." }
    return $actual
}

if ($Build) {
    $goExe = Join-Path $GoRoot 'bin\go.exe'
    foreach ($path in @($sourceArchive, $goExe)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Required pinned build input is missing: $path" }
    }
    $null = Assert-Sha256 $sourceArchive $lock.archive_sha256 'Pinned Amnezia Box source archive'
    $goVersion = (& $goExe version | Out-String).Trim()
    if ($goVersion -ne 'go version go1.25.5 windows/amd64') { throw "Unexpected Go toolchain: $goVersion" }

    # Fresh expansion of the verified archive each run; the removed path is the exact owned src folder.
    if (Test-Path -LiteralPath $srcParent) {
        if (-not $srcParent.StartsWith($workFull, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to remove a path outside the work root.' }
        Remove-Item -LiteralPath $srcParent -Recurse -Force
    }
    New-Item -ItemType Directory -Path $srcParent, $outDir, $goCache -Force | Out-Null
    Expand-Archive -LiteralPath $sourceArchive -DestinationPath $srcParent -Force
    if (-not (Test-Path -LiteralPath (Join-Path $srcRoot 'cmd\sing-box\main.go'))) { throw 'Pinned Amnezia Box source tree is incomplete.' }

    Push-Location $srcRoot
    try {
        $ErrorActionPreference = 'Continue'
        foreach ($name in $lock.windows_patches) {
            $patch = Join-Path $repoRoot ('third_party\amnezia-box\' + $name)
            & git apply --check $patch
            if ($LASTEXITCODE -ne 0) { throw "Pinned patch does not apply cleanly: $name" }
            & git apply $patch
            if ($LASTEXITCODE -ne 0) { throw "Pinned patch failed to apply: $name" }
        }
    } finally { Pop-Location; $ErrorActionPreference = 'Stop' }

    $saved = @{}
    foreach ($name in 'PATH', 'GOPATH', 'GOTOOLCHAIN', 'GOWORK', 'GOCACHE', 'GOOS', 'GOARCH', 'CGO_ENABLED', 'GOFLAGS', 'GOPROXY') {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        $env:PATH = "$(Join-Path $GoRoot 'bin');$($saved['PATH'])"
        $env:GOPATH = (Resolve-Path $GoPath).Path
        $env:GOCACHE = $goCache
        $env:GOTOOLCHAIN = 'local'
        $env:GOWORK = 'off'
        $env:GOOS = 'windows'
        $env:GOARCH = 'amd64'
        $env:CGO_ENABLED = '0'
        $env:GOFLAGS = '-mod=readonly'
        if ($GoProxy) { $env:GOPROXY = $GoProxy }
        $tags = ($lock.windows_tags -join ',')
        $exe = Join-Path $outDir 'detour-engine.exe'
        if (Test-Path -LiteralPath $exe) { Remove-Item -LiteralPath $exe -Force }
        $version = $lock.version.TrimStart('v')
        Push-Location $srcRoot
        # Go prints module download progress on stderr; Windows PowerShell 5.1 would turn that into a
        # terminating error under 'Stop'. The exit code is the result.
        $ErrorActionPreference = 'Continue'
        try {
            & $goExe build -trimpath -tags $tags `
                -ldflags "-X github.com/sagernet/sing-box/constant.Version=$version -s -w -buildid=" `
                -o $exe ./cmd/sing-box
            $buildExit = $LASTEXITCODE
        } finally { Pop-Location; $ErrorActionPreference = 'Stop' }
        if ($buildExit -ne 0) { throw "Engine build failed with exit code $buildExit." }
    } finally {
        foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    }
    if (-not (Test-Path -LiteralPath $exe)) { throw 'Engine build did not produce detour-engine.exe.' }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash.ToLowerInvariant()
    Write-Output "engine=detour-engine.exe; upstream=$($lock.version); tags=$tags; bytes=$((Get-Item -LiteralPath $exe).Length); sha256=$hash"
}

if ($FetchWintun) {
    # Network step: downloads one pinned file. Verified before anything is extracted.
    $pin = $lock.windows_wintun
    New-Item -ItemType Directory -Path $wintunDir -Force | Out-Null
    $zip = Join-Path $wintunDir ('wintun-' + $pin.version + '.zip')
    if (-not (Test-Path -LiteralPath $zip)) {
        $partial = $zip + '.partial'
        Invoke-WebRequest -Uri $pin.url -OutFile $partial -UseBasicParsing
        $partialHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $partial).Hash.ToLowerInvariant()
        if ($partialHash -ne $pin.zip_sha256.ToLowerInvariant()) {
            Remove-Item -LiteralPath $partial -Force
            throw 'Downloaded wintun archive does not match the pinned SHA-256; discarded.'
        }
        Move-Item -LiteralPath $partial -Destination $zip
    }
    $null = Assert-Sha256 $zip $pin.zip_sha256 'wintun archive'
    $extract = Join-Path $wintunDir 'extract'
    if (Test-Path -LiteralPath $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
    $dllSource = Join-Path $extract ($pin.dll_path_in_zip -replace '/', '\')
    if (-not (Test-Path -LiteralPath $dllSource)) { throw 'wintun.dll missing from the pinned archive.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $dllSource
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch [regex]::Escape($pin.signer)) {
        throw 'wintun.dll Authenticode signature is not valid for the expected signer.'
    }
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    Copy-Item -LiteralPath $dllSource -Destination (Join-Path $outDir 'wintun.dll') -Force
    $dllHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $outDir 'wintun.dll')).Hash.ToLowerInvariant()
    Write-Output "wintun=$($pin.version); signer=$($pin.signer); dll_sha256=$dllHash"
}
