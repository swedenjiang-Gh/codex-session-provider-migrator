from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import codex_provider_migrator_cli as cli


def write_session(path: Path, provider: str, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "session_meta", "payload": {"model_provider": provider}},
        {"type": "response_item", "payload": {"marker": marker}},
    ]
    path.write_text(
        "\n".join(json.dumps(record, separators=(",", ":")) for record in records) + "\n",
        encoding="utf-8",
    )


def create_fixture(root: Path) -> None:
    (root / "config.toml").write_text(
        'model_provider = "bingchaai"\n\n'
        '[model_providers.bingchaai]\n'
        'base_url = "http://127.0.0.1:48800/v1"\n\n'
        '[model_providers.custom]\n'
        'base_url = "http://127.0.0.1:49999/v1"\n',
        encoding="utf-8",
    )
    write_session(root / "sessions" / "active.jsonl", "openai", "active")
    write_session(root / "archived_sessions" / "archived.jsonl", "custom", "archived")
    connection = sqlite3.connect(root / "state_5.sqlite")
    try:
        connection.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT NOT NULL, source TEXT)"
        )
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?)",
            [
                ("active", "openai", "vscode"),
                ("archived", "custom", "vscode"),
                ("indexed-only", "bingchaai", "vscode"),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def provider_from_session(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])["payload"][
        "model_provider"
    ]


class ProviderRowsTests(unittest.TestCase):
    def test_rows_include_configured_sessions_and_index_providers(self) -> None:
        records = [
            {"provider": "openai", "group": "sessions"},
            {"provider": "custom", "group": "archived_sessions"},
            {"provider": "custom", "group": "sessions"},
        ]

        rows = cli.provider_rows(
            records,
            configured={"openai", "custom", "bingchaai"},
            index_counts={"openai": 3, "bingchaai": 7, "legacy": 1},
        )

        self.assertEqual(
            rows,
            [
                {"provider": "bingchaai", "active": 0, "archived": 0, "jsonl": 0, "index": 7},
                {"provider": "custom", "active": 1, "archived": 1, "jsonl": 2, "index": 0},
                {"provider": "legacy", "active": 0, "archived": 0, "jsonl": 0, "index": 1},
                {"provider": "openai", "active": 1, "archived": 0, "jsonl": 1, "index": 3},
            ],
        )

    def test_table_contains_each_count_column(self) -> None:
        table = cli.render_provider_table(
            [{"provider": "openai", "active": 2, "archived": 3, "jsonl": 5, "index": 4}]
        )

        self.assertIn("Provider", table)
        self.assertIn("普通会话", table)
        self.assertIn("归档会话", table)
        self.assertIn("JSONL 总数", table)
        self.assertIn("SQLite 索引", table)
        self.assertIn("openai", table)


class ProcessTests(unittest.TestCase):
    def test_tasklist_parser_returns_only_codex_related_processes(self) -> None:
        tasklist = (
            '"Codex.exe","100","Console","1","10,000 K"\n'
            '"notepad.exe","101","Console","1","5,000 K"\n'
            '"ChatGPT.exe","102","Console","1","20,000 K"\n'
        )

        self.assertEqual(cli.codex_processes_from_tasklist(tasklist), ["ChatGPT.exe", "Codex.exe"])


class InteractiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "codex"
        self.root.mkdir()
        create_fixture(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, answers: list[str], processes: list[str] | None = None) -> tuple[int, str]:
        iterator = iter(answers)
        output = io.StringIO()
        result = cli.run_interactive(
            self.root,
            input_fn=lambda prompt: next(iterator),
            output_fn=lambda message="": print(message, file=output),
            process_checker=lambda: [] if processes is None else processes,
        )
        return result, output.getvalue()

    def test_cancel_keeps_sessions_unchanged(self) -> None:
        backup = Path(self.temp.name) / "backups"

        result, output = self.run_cli(["bingchaai", str(backup), "cancel"])

        self.assertEqual(result, 0)
        self.assertIn("已取消", output)
        self.assertEqual(provider_from_session(self.root / "sessions" / "active.jsonl"), "openai")
        self.assertFalse(backup.exists())

    def test_running_codex_blocks_apply(self) -> None:
        backup = Path(self.temp.name) / "backups"

        result, output = self.run_cli(
            ["bingchaai", str(backup), "APPLY"], processes=["Codex.exe"]
        )

        self.assertEqual(result, 3)
        self.assertIn("Codex.exe", output)
        self.assertEqual(provider_from_session(self.root / "sessions" / "active.jsonl"), "openai")
        self.assertFalse(backup.exists())

    def test_apply_migrates_sessions_and_indexes_with_backup(self) -> None:
        backup = Path(self.temp.name) / "backups"

        result, output = self.run_cli(["bingchaai", str(backup), "APPLY"])

        self.assertEqual(result, 0)
        self.assertIn("迁移完成", output)
        self.assertEqual(
            provider_from_session(self.root / "sessions" / "active.jsonl"), "bingchaai"
        )
        self.assertEqual(
            provider_from_session(self.root / "archived_sessions" / "archived.jsonl"),
            "bingchaai",
        )
        connection = sqlite3.connect(self.root / "state_5.sqlite")
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT model_provider, COUNT(*) FROM threads GROUP BY model_provider"
                ).fetchall(),
                [("bingchaai", 3)],
            )
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()
        backup_directories = list(backup.glob("provider-migration-*"))
        self.assertEqual(len(backup_directories), 1)
        self.assertTrue((backup_directories[0] / "sessions.zip").is_file())
        self.assertTrue((backup_directories[0] / "state_5.sqlite").is_file())


if __name__ == "__main__":
    unittest.main()
