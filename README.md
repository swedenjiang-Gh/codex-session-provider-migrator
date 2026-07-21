# Codex Session Provider Migrator

在 Windows 上批量同步 Codex 本地会话文件和 SQLite 索引中的 `model_provider`。用于切换代理或自定义 provider 后，历史会话仍存在但被侧边栏过滤的情况。

## 处理范围

- 扫描 `%USERPROFILE%\.codex\sessions` 和 `archived_sessions` 下的 JSONL。
- 只修改首行 `session_meta.payload.model_provider`，保留会话正文。
- 同步 `state_5.sqlite` 中 `threads.model_provider`。
- 执行前备份待修改的会话和 SQLite 数据库。
- 迁移后重新解析会话，并执行 `PRAGMA integrity_check`。

脚本不会上传会话内容，也不会修改 `config.toml`。

## 要求

- Windows 和 PowerShell 7（`pwsh.exe`）。
- Codex 自带 Python 运行时，或 PATH 中的 Python 3.11 及以上版本。
- 目标 provider 已在 `%USERPROFILE%\.codex\config.toml` 中配置；内置 `openai` 除外。

## 使用

交互执行：

```powershell
pwsh.exe -NoLogo -NoProfile -File .\Set-CodexSessionProvider.ps1
```

输入目标 provider 后，脚本先显示预览。确认无误时输入 `APPLY`。真正迁移前必须完全退出 Codex；脚本检测到相关进程仍在运行时会拒绝修改。

非交互执行：

```powershell
pwsh.exe -NoLogo -NoProfile -File .\Set-CodexSessionProvider.ps1 `
  -TargetProvider bingchaai `
  -Apply `
  -NonInteractive `
  -BackupRoot D:\codex\backups
```

默认 Codex 数据目录是 `%USERPROFILE%\.codex`，默认备份目录是 `D:\codex\backups`。可分别通过 `-CodexRoot` 和 `-BackupRoot` 指定其他路径。

## 测试

测试使用独立临时夹具，不接触真实 Codex 数据：

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -File .\tests\Test-Set-CodexSessionProvider.ps1
```

测试覆盖预览不修改数据、普通与归档会话迁移、正文哈希不变、SQLite 同步、完整性检查和备份生成。

## 注意

- 不要在 Codex 正在写入会话时使用 `-SkipProcessCheck`；该参数仅用于隔离测试。
- 备份目录包含 `sessions.zip`、`state_5.sqlite` 和迁移清单，请确认备份有效后再清理。
- 如果迁移后侧边栏没有立即刷新，完全退出并重新打开 Codex。

## License

[MIT](LICENSE)
