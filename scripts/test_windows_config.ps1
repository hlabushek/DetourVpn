[CmdletBinding()]
param(
    [string]$VsRoot = 'C:\Program Files\Microsoft Visual Studio\2022\Community'
)
# Builds and runs the I-032 Windows config-factory tests (MSVC + CMake + Ninja from Visual Studio) and
# the gate helper detour_config_tool.exe. Sources are copied outside OneDrive first.
$ErrorActionPreference = 'Stop'
$windows = (Resolve-Path (Join-Path $PSScriptRoot '..\apps\client\windows')).Path
$root = Join-Path $env:LOCALAPPDATA 'ProjectVpn\cpp-tests'
$stage = Join-Path $root 'src'
$build = Join-Path $root ('build-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
foreach ($path in @($stage) + @(Get-ChildItem -LiteralPath $root -Directory -Filter 'build*' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })) {
    if (Test-Path -LiteralPath $path) {
        if (-not $path.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to remove a path outside the work root.' }
        try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop } catch { Write-Warning "Could not remove old build dir (in use): $path" }
    }
}
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item -Recurse (Join-Path $windows 'runner') (Join-Path $stage 'runner')
Copy-Item -Recurse (Join-Path $windows 'tests') (Join-Path $stage 'tests')
$vcvars = Join-Path $VsRoot 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path -LiteralPath $vcvars)) { throw 'Visual Studio C++ tools were not found.' }
$cmdFile = Join-Path $root 'run.cmd'
@"
@echo off
set "PATH=C:\Program Files (x86)\Microsoft Visual Studio\Installer;%PATH%"
call "$vcvars" >nul
if errorlevel 1 exit /b 1
set "CM=$VsRoot\Common7\IDE\CommonExtensions\Microsoft\CMake"
set "PATH=%CM%\CMake\bin;%CM%\Ninja;%PATH%"
cmake -S "$stage\tests" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl || exit /b 1
cmake --build "$build" || exit /b 1
ctest --test-dir "$build" --output-on-failure --timeout 60
"@ | Set-Content -Encoding ASCII $cmdFile
& cmd /c $cmdFile
if ($LASTEXITCODE -ne 0) { throw "C++ tests failed with exit code $LASTEXITCODE." }
Write-Output "tool=$(Join-Path $build 'detour_config_tool.exe')"
