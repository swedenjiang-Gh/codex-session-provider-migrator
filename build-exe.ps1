[CmdletBinding()]
param(
    [string]$Python
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Python)) {
    $runtimeRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes'
    if (Test-Path -LiteralPath $runtimeRoot -PathType Container) {
        foreach ($runtime in Get-ChildItem -LiteralPath $runtimeRoot -Directory) {
            $candidate = Join-Path $runtime.FullName 'dependencies\python\python.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $Python = $candidate
                break
            }
        }
    }
}

if ([string]::IsNullOrWhiteSpace($Python)) {
    $pythonCommand = Get-Command 'python.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $pythonCommand) {
        $Python = $pythonCommand.Source
    }
}

if ([string]::IsNullOrWhiteSpace($Python) -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw '找不到用于构建的 Python 3.11 或更高版本。'
}

$entryPoint = Join-Path $PSScriptRoot 'codex_provider_migrator_cli.py'
$distPath = Join-Path $PSScriptRoot 'dist'
$workPath = Join-Path $PSScriptRoot 'build\pyinstaller'
$specPath = Join-Path $PSScriptRoot 'build'
$nativeArguments = @(
    '-m'
    'PyInstaller'
    '--noconfirm'
    '--clean'
    '--onefile'
    '--console'
    '--noupx'
    '--name'
    'CodexSessionProviderMigrator'
    '--distpath'
    $distPath
    '--workpath'
    $workPath
    '--specpath'
    $specPath
    $entryPoint
)

& $Python @nativeArguments
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    throw "PyInstaller 构建失败，退出码：$exitCode"
}

$executable = Join-Path $distPath 'CodexSessionProviderMigrator.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "构建未生成目标文件：$executable"
}

Write-Output "构建完成：$executable"
