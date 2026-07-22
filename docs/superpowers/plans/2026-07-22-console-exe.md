# Console EXE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a self-contained Windows console EXE for inspecting provider counts and safely migrating Codex session provider labels.

**Architecture:** Add a small console layer over the existing Python migration engine. Package both modules into one executable with PyInstaller; keep all runtime dependencies inside the EXE and retain the existing backup and integrity guarantees.

**Tech Stack:** Python 3.12 standard library, `unittest`, PyInstaller, Windows PowerShell 5.1 build helpers, GitHub Releases.

## Global Constraints

- End users install no runtime or dependencies.
- The EXE does not invoke PowerShell.
- Build and test helpers must run under Windows PowerShell 5.1.
- No session content, database, backup, secret, or machine-specific path enters Git.
- Backup and integrity verification remain mandatory before completion.

---

### Task 1: Provider summary and console behavior

**Files:**
- Create: `tests/test_cli.py`
- Create: `codex_provider_migrator_cli.py`

**Interfaces:**
- Consumes: `session_records(root)`, `thread_counts(database_path)`, and `configured_providers(config_path)` from `codex_session_provider.py`.
- Produces: `provider_rows(...)`, `render_provider_table(...)`, `running_codex_processes(...)`, and `run_interactive(...)`.

- [ ] Write tests asserting the union of configured, JSONL, and SQLite providers and the active/archived/index counts.
- [ ] Run `python -m unittest tests.test_cli -v` and verify failure because the CLI module does not exist.
- [ ] Implement only the summary, rendering, validation, process-check, confirmation, and orchestration behavior required by the tests.
- [ ] Rerun the CLI tests and the existing PowerShell fixture test.

### Task 2: Self-contained executable build

**Files:**
- Create: `requirements-build.txt`
- Create: `build-exe.ps1`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: repository Python modules and a Python environment containing the pinned PyInstaller version.
- Produces: `dist/CodexSessionProviderMigrator.exe`.

- [ ] Add a Windows PowerShell 5.1-compatible build script that invokes `python -m PyInstaller` with structured arguments.
- [ ] Install build-only dependencies into `D:\codex\tooling\codex-session-provider-migrator-venv`.
- [ ] Build the one-file console EXE and confirm no PowerShell executable is launched by the application.

### Task 3: Packaged behavior verification

**Files:**
- Create: `tests/exe_fixture.py`

**Interfaces:**
- Consumes: `CodexSessionProviderMigrator.exe` and an isolated Codex fixture root.
- Produces: deterministic preview/apply assertions and a release-ready SHA-256 checksum.

- [ ] Create a fixture containing active and archived JSONL sessions, a configured provider, and a SQLite `threads` table.
- [ ] Run the EXE with redirected input to verify table counts, cancellation, apply confirmation, backup creation, provider replacement, and database integrity.
- [ ] Verify the executable's architecture, size, SHA-256, and absence of secrets or machine-specific paths in tracked source files.

### Task 4: Documentation and release

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: verified executable name and usage.
- Produces: end-user download instructions and GitHub Release asset.

- [ ] Make the EXE the primary usage path and state that no environment installation is required.
- [ ] Run all tests and `git diff --check`.
- [ ] Commit and push the source changes.
- [ ] Publish the EXE and SHA-256 file as a GitHub Release, then verify anonymous asset access.
