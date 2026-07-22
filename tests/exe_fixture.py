from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_cli import create_fixture, provider_from_session


def run_executable(executable: Path, root: Path, answers: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(executable),
            "--codex-root",
            str(root),
            "--skip-process-check",
            "--no-pause",
        ],
        input=answers,
        text=True,
        capture_output=True,
        timeout=60,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    arguments = parser.parse_args()
    executable = arguments.executable.resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)

    with tempfile.TemporaryDirectory(prefix="codex-provider-exe-") as temporary:
        fixture_root = Path(temporary) / "codex"
        fixture_root.mkdir()
        create_fixture(fixture_root)
        backup_root = Path(temporary) / "backups"

        cancelled = run_executable(
            executable,
            fixture_root,
            f"bingchaai\n{backup_root}\ncancel\n",
        )
        if cancelled.returncode != 0 or "已取消" not in cancelled.stdout:
            raise RuntimeError(
                f"Cancellation check failed: code={cancelled.returncode}\n"
                f"stdout={cancelled.stdout}\nstderr={cancelled.stderr}"
            )
        if provider_from_session(fixture_root / "sessions" / "active.jsonl") != "openai":
            raise RuntimeError("Cancellation changed an active session.")
        if backup_root.exists():
            raise RuntimeError("Cancellation created a backup directory.")

        applied = run_executable(
            executable,
            fixture_root,
            f"bingchaai\n{backup_root}\nAPPLY\n",
        )
        required_output = ("Provider", "普通会话", "归档会话", "SQLite 索引", "迁移完成")
        if applied.returncode != 0 or any(text not in applied.stdout for text in required_output):
            raise RuntimeError(
                f"Apply check failed: code={applied.returncode}\n"
                f"stdout={applied.stdout}\nstderr={applied.stderr}"
            )

        for relative_path in (
            Path("sessions/active.jsonl"),
            Path("archived_sessions/archived.jsonl"),
        ):
            if provider_from_session(fixture_root / relative_path) != "bingchaai":
                raise RuntimeError(f"Provider was not migrated: {relative_path}")

        connection = sqlite3.connect(fixture_root / "state_5.sqlite")
        try:
            providers = connection.execute(
                "SELECT model_provider, COUNT(*) FROM threads GROUP BY model_provider"
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connection.close()
        if providers != [("bingchaai", 3)] or integrity != "ok":
            raise RuntimeError(f"SQLite verification failed: {providers}, {integrity}")

        backup_directories = list(backup_root.glob("provider-migration-*"))
        if len(backup_directories) != 1:
            raise RuntimeError(f"Expected one backup directory, found {len(backup_directories)}")
        if not (backup_directories[0] / "sessions.zip").is_file():
            raise RuntimeError("Session backup ZIP is missing.")
        if not (backup_directories[0] / "state_5.sqlite").is_file():
            raise RuntimeError("SQLite backup is missing.")

        print(
            json.dumps(
                {
                    "executable": str(executable),
                    "provider_rows_visible": True,
                    "cancel_verified": True,
                    "apply_verified": True,
                    "backup_verified": True,
                    "sqlite_integrity": integrity,
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
