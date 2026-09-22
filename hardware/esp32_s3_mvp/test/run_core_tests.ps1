$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $projectRoot '.pio/native-tests'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) { throw 'Install Visual Studio C++ Build Tools to run these native tests.' }
$vsRoot = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsRoot) { throw 'Visual Studio C++ toolchain not found.' }
Import-Module (Join-Path $vsRoot 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
Enter-VsDevShell -VsInstallPath $vsRoot -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
$source = Join-Path $PSScriptRoot 'core_test.cpp'
$include = Join-Path $projectRoot 'include'
$exe = Join-Path $outDir 'core_test.exe'
$obj = Join-Path $outDir 'core_test.obj'
& cl.exe /nologo /EHsc /std:c++14 /W4 "/I$include" $source "/Fe:$exe" "/Fo:$obj"
if ($LASTEXITCODE -ne 0) { throw 'Native test compilation failed.' }
& $exe
if ($LASTEXITCODE -ne 0) { throw 'Native tests failed.' }
