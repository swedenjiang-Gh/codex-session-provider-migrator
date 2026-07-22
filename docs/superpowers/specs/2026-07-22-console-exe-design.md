# Console EXE Design

## Goal

Provide a single Windows executable that lets a user inspect Codex provider usage and safely migrate all local session provider labels without installing Python, PowerShell 7, .NET, or any other runtime.

## User experience

The console displays one row per provider found in `config.toml`, JSONL session metadata, or `state_5.sqlite`. Each row shows active-session, archived-session, JSONL-total, and SQLite-index counts.

The user enters a configured target provider and a backup directory. The application prints the planned changes and requires the exact confirmation `APPLY`. It refuses to apply while Codex or ChatGPT is running.

## Architecture

- `codex_session_provider.py` remains the migration engine and owns JSONL parsing, backups, provider replacement, body-hash verification, and SQLite integrity checks.
- `codex_provider_migrator_cli.py` owns provider-count aggregation, console rendering, input validation, process detection, confirmation, and orchestration.
- PyInstaller produces `CodexSessionProviderMigrator.exe` as a one-file console executable containing Python and all required standard-library modules.

The executable does not call PowerShell at runtime. Windows PowerShell 5.1 is used only by optional repository build and test helpers.

## Safety

- The target provider must exist in `config.toml`, except built-in `openai`.
- Applying requires an explicit `APPLY` confirmation.
- Running Codex/ChatGPT processes block the write path.
- A verified SQLite backup and ZIP archive are created before changes.
- Only the first JSONL `session_meta` line and `threads.model_provider` are changed.
- Conversation body hashes and `PRAGMA integrity_check` are verified after migration.

## Testing and delivery

Unit tests cover provider count aggregation, table output, input validation, process parsing, cancellation, and migration orchestration against an isolated fixture. The packaged EXE is then run against a separate fixture in preview and apply modes. The release asset is published on GitHub with a SHA-256 checksum.
