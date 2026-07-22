[CmdletBinding()]
param(
    [string]$TargetProvider,
    [switch]$Apply,
    [switch]$NonInteractive,
    [string]$CodexRoot = (Join-Path $env:USERPROFILE '.codex'),
    [string]$BackupRoot = 'D:\codex\backups',
    [switch]$SkipProcessCheck,
    [switch]$WaitForCodexExit
)

$ErrorActionPreference = 'Stop'

function Get-CodexPython {
    $runtimeRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes'
    if (Test-Path -LiteralPath $runtimeRoot -PathType Container) {
        foreach ($runtime in Get-ChildItem -LiteralPath $runtimeRoot -Directory) {
            $candidate = Join-Path $runtime.FullName 'dependencies\python\python.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return $candidate
            }
        }
    }

    $python = Get-Command 'python.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $python) {
        return $python.Source
    }

    throw '找不到 Codex 内置 Python 运行时或 PATH 中的 python.exe。需要 Python 3.11 或更高版本。'
}

function Get-CodexDesktopProcesses {
    @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -in @('ChatGPT', 'Codex', 'CodexDesktop') })
}

if ([string]::IsNullOrWhiteSpace($TargetProvider)) {
    if ($NonInteractive) {
        throw 'NonInteractive 模式必须提供 -TargetProvider。'
    }
    $TargetProvider = Read-Host '输入目标 provider'
}

if ([string]::IsNullOrWhiteSpace($TargetProvider)) {
    throw '目标 provider 不能为空。'
}

if ($Apply -and -not $SkipProcessCheck) {
    while ($true) {
        $running = Get-CodexDesktopProcesses
        if ($running.Count -eq 0) {
            break
        }
        if (-not $WaitForCodexExit) {
            throw ('请先完全退出 Codex/ChatGPT，再重新执行。仍在运行：' + (($running | Select-Object -ExpandProperty ProcessName -Unique) -join ', '))
        }
        Start-Sleep -Seconds 2
    }
}

$scriptDirectory = Split-Path -Parent $PSCommandPath
$enginePath = Join-Path $scriptDirectory 'codex_session_provider.py'
if (-not (Test-Path -LiteralPath $enginePath -PathType Leaf)) {
    throw "找不到迁移引擎：$enginePath"
}

$python = Get-CodexPython
$engineArguments = @(
    $enginePath,
    '--root', $CodexRoot,
    '--backup-root', $BackupRoot,
    '--target', $TargetProvider
)

if ($Apply) {
    $engineArguments += '--apply'
}

& $python @engineArguments
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    throw "Provider 迁移失败，退出码：$exitCode"
}

if (-not $Apply -and -not $NonInteractive) {
    $confirmation = Read-Host '输入 APPLY 执行迁移；直接回车退出'
    if ($confirmation -eq 'APPLY') {
        & $PSCommandPath -TargetProvider $TargetProvider -Apply
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "Provider 迁移失败，退出码：$exitCode"
        }
    }
}
