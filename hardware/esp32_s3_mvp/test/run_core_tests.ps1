$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $projectRoot '.pio/native-tests'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$gcc = Get-Command g++ -ErrorAction SilentlyContinue
if ($gcc) {
    foreach ($test in @('core_test', 'motor_test')) {
        $source = Join-Path $PSScriptRoot "$test.cpp"
        $exe = Join-Path $outDir "$test.exe"
        & $gcc.Source -std=c++14 -Wall -Wextra -Werror -static "-I$(Join-Path $projectRoot 'include')" $source -o $exe
        if ($LASTEXITCODE -ne 0) { throw "Native test compilation failed: $test" }
        & $exe
        if ($LASTEXITCODE -ne 0) { throw "Native tests failed: $test" }
    }
    exit 0
}
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
$source = Join-Path $PSScriptRoot 'motor_test.cpp'
$exe = Join-Path $outDir 'motor_test.exe'
$obj = Join-Path $outDir 'motor_test.obj'
& cl.exe /nologo /EHsc /std:c++14 /W4 "/I$include" $source "/Fe:$exe" "/Fo:$obj"
if ($LASTEXITCODE -ne 0) { throw 'Motor test compilation failed.' }
& $exe
if ($LASTEXITCODE -ne 0) { throw 'Motor tests failed.' }
