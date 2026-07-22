#!/usr/bin/env python3
"""Interactive console frontend for Codex session provider migration."""

from __future__ import annotations

import argparse
import csv
import io
import locale
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Iterable

from codex_session_provider import (
    configured_providers,
    create_backup,
    prepare_session_updates,
    replace_sessions,
    session_records,
    thread_counts,
    update_threads,
    verify,
)


Record = dict[str, object]
OutputFunction = Callable[[str], None]


def provider_rows(
    records: Iterable[Record],
    configured: set[str],
    index_counts: dict[str, int],
) -> list[dict[str, int | str]]:
    records = list(records)
    providers = configured | set(index_counts)
    providers.update(str(record["provider"]) for record in records)

    rows: list[dict[str, int | str]] = []
    for provider in sorted(providers, key=str.casefold):
        active = sum(
            1
            for record in records
            if record["provider"] == provider and record["group"] == "sessions"
        )
        archived = sum(
            1
            for record in records
            if record["provider"] == provider and record["group"] == "archived_sessions"
        )
        rows.append(
            {
                "provider": provider,
                "active": active,
                "archived": archived,
                "jsonl": active + archived,
                "index": index_counts.get(provider, 0),
            }
        )
    return rows


def render_provider_table(rows: Iterable[dict[str, int | str]]) -> str:
    headers = ("Provider", "普通会话", "归档会话", "JSONL 总数", "SQLite 索引")
    values = [
        (
            str(row["provider"]),
            str(row["active"]),
            str(row["archived"]),
            str(row["jsonl"]),
            str(row["index"]),
        )
        for row in rows
    ]
    widths = [
        max(len(headers[column]), *(len(row[column]) for row in values))
        if values
        else len(headers[column])
        for column in range(len(headers))
    ]

    def format_row(row: tuple[str, ...]) -> str:
        return " | ".join(value.ljust(widths[column]) for column, value in enumerate(row))

    separator = "-+-".join("-" * width for width in widths)
    return "\n".join([format_row(headers), separator, *(format_row(row) for row in values)])


def codex_processes_from_tasklist(tasklist_output: str) -> list[str]:
    related = {"chatgpt.exe", "codex.exe", "codexdesktop.exe"}
    found = {
        row[0]
        for row in csv.reader(io.StringIO(tasklist_output))
        if row and row[0].casefold() in related
    }
    return sorted(found, key=str.casefold)


def running_codex_processes() -> list[str]:
    result = subprocess.run(
        ["tasklist.exe", "/FO", "CSV", "/NH"],
        check=True,
        capture_output=True,
    )
    encoding = locale.getpreferredencoding(False) or "utf-8"
    return codex_processes_from_tasklist(result.stdout.decode(encoding, errors="replace"))


def default_backup_root() -> Path:
    d_drive = Path("D:/")
    if d_drive.is_dir():
        return d_drive / "codex" / "backups"
    return Path.home() / "codex-backups"


def run_interactive(
    codex_root: Path,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: OutputFunction = print,
    process_checker: Callable[[], list[str]] = running_codex_processes,
) -> int:
    codex_root = codex_root.expanduser().resolve()
    config_path = codex_root / "config.toml"
    database_path = codex_root / "state_5.sqlite"
    if not config_path.is_file():
        raise FileNotFoundError(f"找不到配置文件：{config_path}")
    if not database_path.is_file():
        raise FileNotFoundError(f"找不到会话索引：{database_path}")

    records = session_records(codex_root)
    configured = configured_providers(config_path)
    index_counts = thread_counts(database_path)

    output_fn("\n当前 provider 与会话数量：")
    output_fn(render_provider_table(provider_rows(records, configured, index_counts)))
    output_fn("")

    target = input_fn("输入目标 provider：").strip()
    if target not in configured:
        output_fn(f"目标 provider 未配置：{target or '<空>'}")
        output_fn("可用 provider：" + ", ".join(sorted(configured, key=str.casefold)))
        return 2

    default_backup = default_backup_root()
    backup_text = input_fn(f"输入备份目录（直接回车使用 {default_backup}）：").strip()
    backup_root = Path(
        os.path.expandvars(os.path.expanduser(backup_text))
        if backup_text
        else default_backup
    ).resolve()

    changes = [record for record in records if record["provider"] != target]
    index_changes = sum(count for provider, count in index_counts.items() if provider != target)
    output_fn("\n迁移预览：")
    output_fn(f"目标 provider：{target}")
    output_fn(f"JSONL 待修改：{len(changes)}")
    output_fn(f"SQLite 索引待修改：{index_changes}")
    output_fn(f"备份目录：{backup_root}")

    if not changes and index_changes == 0:
        output_fn("当前数据已经全部使用目标 provider，无需迁移。")
        return 0

    confirmation = input_fn("输入 APPLY 执行迁移，输入其他内容取消：").strip()
    if confirmation != "APPLY":
        output_fn("已取消，未修改任何数据。")
        return 0

    running = process_checker()
    if running:
        output_fn("检测到 Codex/ChatGPT 正在运行，请完全退出后重试：")
        output_fn(", ".join(running))
        return 3

    backup_directory = create_backup(changes, codex_root, database_path, backup_root, target)
    prepared = prepare_session_updates(changes, target)
    replace_sessions(prepared)
    update_threads(database_path, target)
    result = verify(codex_root, database_path, target, prepared)

    output_fn("迁移完成。")
    output_fn(f"会话文件：{result['session_files']}")
    output_fn(f"备份位置：{backup_directory}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex 会话 provider 批量迁移工具")
    parser.add_argument(
        "--codex-root",
        type=Path,
        default=Path.home() / ".codex",
        help="Codex 数据目录，默认是 %%USERPROFILE%%\\.codex",
    )
    parser.add_argument(
        "--skip-process-check",
        action="store_true",
        help="仅用于隔离测试；真实数据迁移不要使用",
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="结束时不等待按 Enter，供自动化测试使用",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        checker = (lambda: []) if arguments.skip_process_check else running_codex_processes
        return run_interactive(arguments.codex_root, process_checker=checker)
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    finally:
        if not arguments.no_pause:
            try:
                input("\n按 Enter 退出...")
            except EOFError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
