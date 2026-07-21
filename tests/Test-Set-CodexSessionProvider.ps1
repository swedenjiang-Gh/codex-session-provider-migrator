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

function Get-TailHash {
    param([string]$Path)

    $python = Get-CodexPython
    $code = @'
import hashlib
import sys

with open(sys.argv[1], "rb") as handle:
    handle.readline()
    digest = hashlib.sha256()
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
print(digest.hexdigest())
'@
    & $python -c $code $Path
    if ($LASTEXITCODE -ne 0) {
        throw "无法计算会话正文哈希：$Path"
    }
}

$scriptRoot = Split-Path -Parent $PSScriptRoot
$mainScript = Join-Path $scriptRoot 'Set-CodexSessionProvider.ps1'
$fixtureRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('codex-provider-test-' + [guid]::NewGuid())
$activeDirectory = Join-Path $fixtureRoot 'sessions\2026\01\01'
$archivedDirectory = Join-Path $fixtureRoot 'archived_sessions\2025\12\31'
$backupRoot = Join-Path $fixtureRoot 'backups'
New-Item -ItemType Directory -Force -Path $activeDirectory, $archivedDirectory, $backupRoot | Out-Null

$activeFile = Join-Path $activeDirectory 'active.jsonl'
$archivedFile = Join-Path $archivedDirectory 'archived.jsonl'
$firstLine = '{"type":"session_meta","payload":{"model_provider":"openai"}}'
$bodyLines = @(
    '{"type":"response_item","payload":{"type":"message","role":"assistant","content":"keep-active"}}',
    '{"type":"event_msg","payload":{"type":"task_complete"}}'
)
[System.IO.File]::WriteAllText($activeFile, (($firstLine, $bodyLines -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText($archivedFile, (($firstLine, $bodyLines -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText((Join-Path $fixtureRoot 'config.toml'), @'
model_provider = "bingchaai"

[model_providers.bingchaai]
base_url = "http://127.0.0.1:48800/v1"
'@, [System.Text.UTF8Encoding]::new($false))

$python = Get-CodexPython
$createDatabase = @'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT NOT NULL, source TEXT)")
conn.execute("INSERT INTO threads VALUES ('active', 'openai', 'vscode')")
conn.execute("INSERT INTO threads VALUES ('archived', 'openai', 'vscode')")
conn.commit()
conn.close()
'@
& $python -c $createDatabase (Join-Path $fixtureRoot 'state_5.sqlite')
if ($LASTEXITCODE -ne 0) {
    throw '无法创建 SQLite 测试夹具。'
}

$activeBodyHash = Get-TailHash -Path $activeFile
$archivedBodyHash = Get-TailHash -Path $archivedFile

& $mainScript -TargetProvider 'bingchaai' -NonInteractive -CodexRoot $fixtureRoot -BackupRoot $backupRoot -SkipProcessCheck
if ($LASTEXITCODE -ne 0) {
    throw '预览模式失败。'
}

if (((Get-Content -LiteralPath $activeFile -First 1 | ConvertFrom-Json).payload.model_provider) -ne 'openai') {
    throw '预览模式修改了普通会话。'
}

& $mainScript -TargetProvider 'bingchaai' -Apply -NonInteractive -CodexRoot $fixtureRoot -BackupRoot $backupRoot -SkipProcessCheck
if ($LASTEXITCODE -ne 0) {
    throw '执行模式失败。'
}

foreach ($file in @($activeFile, $archivedFile)) {
    if (((Get-Content -LiteralPath $file -First 1 | ConvertFrom-Json).payload.model_provider) -ne 'bingchaai') {
        throw "会话 provider 未迁移：$file"
    }
}

if ((Get-TailHash -Path $activeFile) -ne $activeBodyHash -or (Get-TailHash -Path $archivedFile) -ne $archivedBodyHash) {
    throw '会话正文在迁移后发生变化。'
}

$verifyDatabase = @'
import sqlite3
import sys

conn = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
providers = {row[0] for row in conn.execute("SELECT DISTINCT model_provider FROM threads")}
integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
conn.close()
if providers != {"bingchaai"} or integrity != "ok":
    raise SystemExit(1)
'@
& $python -c $verifyDatabase (Join-Path $fixtureRoot 'state_5.sqlite')
if ($LASTEXITCODE -ne 0) {
    throw 'SQLite provider 或完整性验证失败。'
}

$backupDirectories = @(Get-ChildItem -LiteralPath $backupRoot -Directory)
if ($backupDirectories.Count -ne 1) {
    throw '未创建唯一的迁移备份目录。'
}

if (-not (Test-Path -LiteralPath (Join-Path $backupDirectories[0].FullName 'sessions.zip') -PathType Leaf) -or -not (Test-Path -LiteralPath (Join-Path $backupDirectories[0].FullName 'state_5.sqlite') -PathType Leaf)) {
    throw '迁移备份不完整。'
}

Write-Output "PASS: provider migration fixture verified at $fixtureRoot"
