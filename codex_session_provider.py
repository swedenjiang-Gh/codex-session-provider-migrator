#!/usr/bin/env python3
"""Safely synchronize Codex session provider labels with the configured provider."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tomllib
import zipfile


def first_line(path: Path) -> tuple[bytes, dict[str, object], bytes]:
    with path.open("rb") as handle:
        raw = handle.readline()
    if not raw:
        raise ValueError(f"Empty session file: {path}")
    if raw.endswith(b"\r\n"):
        ending = b"\r\n"
        payload = raw[:-2]
    elif raw.endswith(b"\n"):
        ending = b"\n"
        payload = raw[:-1]
    else:
        ending = b""
        payload = raw
    bom = b"\xef\xbb\xbf" if payload.startswith(b"\xef\xbb\xbf") else b""
    data = json.loads(payload[len(bom) :].decode("utf-8"))
    if data.get("type") != "session_meta" or not isinstance(data.get("payload"), dict):
        raise ValueError(f"First JSON line is not session metadata: {path}")
    return raw, data, ending


def body_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        handle.readline()
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def session_records(root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for group in ("sessions", "archived_sessions"):
        folder = root / group
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.jsonl")):
            raw, metadata, ending = first_line(path)
            provider = metadata["payload"].get("model_provider")
            if not isinstance(provider, str) or not provider:
                raise ValueError(f"Session has no model_provider: {path}")
            stat = path.stat()
            records.append(
                {
                    "path": path,
                    "group": group,
                    "provider": provider,
                    "raw_first_line": raw,
                    "metadata": metadata,
                    "ending": ending,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
    return records


def provider_counts(records: list[dict[str, object]]) -> dict[str, int]:
    return dict(collections.Counter(str(record["provider"]) for record in records))


def thread_counts(database_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        return dict(
            conn.execute(
                "SELECT COALESCE(model_provider, '<missing>'), COUNT(*) "
                "FROM threads GROUP BY model_provider ORDER BY model_provider"
            ).fetchall()
        )
    finally:
        conn.close()


def configured_providers(config_path: Path) -> set[str]:
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    names = set((config.get("model_providers") or {}).keys())
    names.add("openai")
    return names


def make_first_line(record: dict[str, object], target: str) -> bytes:
    metadata = record["metadata"]
    assert isinstance(metadata, dict)
    payload = metadata["payload"]
    assert isinstance(payload, dict)
    payload["model_provider"] = target
    raw = record["raw_first_line"]
    assert isinstance(raw, bytes)
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    ending = record["ending"]
    assert isinstance(ending, bytes)
    return bom + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + ending


def backup_database(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(source_path, timeout=30)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite backup integrity check failed: {integrity}")
    finally:
        destination.close()
        source.close()


def create_backup(records: list[dict[str, object]], root: Path, database_path: Path, backup_root: Path, target: str) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = backup_root / f"provider-migration-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_database(database_path, backup_dir / "state_5.sqlite")

    archive_path = backup_dir / "sessions.zip"
    partial_archive = backup_dir / "sessions.zip.partial"
    manifest: list[dict[str, object]] = []
    with zipfile.ZipFile(partial_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for record in records:
            path = record["path"]
            assert isinstance(path, Path)
            before = path.stat()
            archive_name = path.relative_to(root).as_posix()
            archive.write(path, archive_name)
            after = path.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise RuntimeError(f"Session changed while backing up: {path}")
            manifest.append(
                {
                    "path": str(path),
                    "archive_path": archive_name,
                    "provider": record["provider"],
                    "size": before.st_size,
                    "tail_sha256": body_hash(path),
                }
            )
        archive.writestr(
            "migration-manifest.json",
            json.dumps({"target_provider": target, "files": manifest}, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    with zipfile.ZipFile(partial_archive, "r") as archive:
        bad = archive.testzip()
    if bad is not None:
        raise RuntimeError(f"Backup ZIP CRC failed: {bad}")
    os.replace(partial_archive, archive_path)
    return backup_dir


def prepare_session_updates(records: list[dict[str, object]], target: str) -> list[dict[str, object]]:
    prepared: list[dict[str, object]] = []
    for record in records:
        path = record["path"]
        assert isinstance(path, Path)
        temp = Path(f"{path}.provider-migration.tmp")
        if temp.exists():
            raise RuntimeError(f"Temporary migration file already exists: {temp}")
        before = path.stat()
        new_first = make_first_line(record, target)
        digest = hashlib.sha256()
        with path.open("rb") as source, temp.open("xb") as destination:
            source.readline()
            destination.write(new_first)
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise RuntimeError(f"Session changed while preparing migration: {path}")
        if digest.hexdigest() != body_hash(path):
            raise RuntimeError(f"Session body changed while preparing migration: {path}")
        raw, metadata, _ = first_line(temp)
        del raw
        if metadata["payload"].get("model_provider") != target:
            raise RuntimeError(f"Prepared provider verification failed: {path}")
        prepared.append(
            {
                "path": path,
                "temp": temp,
                "size": before.st_size,
                "mtime_ns": before.st_mtime_ns,
                "tail_sha256": digest.hexdigest(),
            }
        )
    return prepared


def replace_sessions(prepared: list[dict[str, object]]) -> None:
    for item in prepared:
        path = item["path"]
        assert isinstance(path, Path)
        current = path.stat()
        if current.st_size != item["size"] or current.st_mtime_ns != item["mtime_ns"]:
            raise RuntimeError(f"Session changed before replacement: {path}")
    for item in prepared:
        path = item["path"]
        temp = item["temp"]
        assert isinstance(path, Path) and isinstance(temp, Path)
        os.replace(temp, path)


def update_threads(database_path: Path, target: str) -> None:
    conn = sqlite3.connect(database_path, timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE threads SET model_provider = ? WHERE model_provider IS NOT ?", (target, target))
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            conn.rollback()
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        conn.commit()
    finally:
        conn.close()


def verify(root: Path, database_path: Path, target: str, prepared: list[dict[str, object]]) -> dict[str, object]:
    records = session_records(root)
    mismatches = [str(record["path"]) for record in records if record["provider"] != target]
    for item in prepared:
        path = item["path"]
        assert isinstance(path, Path)
        if body_hash(path) != item["tail_sha256"]:
            mismatches.append(f"Conversation body changed: {path}")
    conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        remaining = conn.execute("SELECT COUNT(*) FROM threads WHERE model_provider IS NOT ?", (target,)).fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    if mismatches or remaining or integrity != "ok":
        raise RuntimeError(
            f"Verification failed: session_mismatches={len(mismatches)}, thread_mismatches={remaining}, integrity={integrity}"
        )
    return {"session_files": len(records), "session_provider_counts": provider_counts(records), "thread_provider_counts": thread_counts(database_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--backup-root", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    backup_root = Path(args.backup_root).resolve()
    config_path = root / "config.toml"
    database_path = root / "state_5.sqlite"
    if args.target not in configured_providers(config_path):
        raise ValueError(f"Target provider is not configured: {args.target}")
    if not database_path.is_file():
        raise FileNotFoundError(database_path)

    records = session_records(root)
    before = {"session_provider_counts": provider_counts(records), "thread_provider_counts": thread_counts(database_path)}
    changes = [record for record in records if record["provider"] != args.target]
    print(json.dumps({"mode": "apply" if args.apply else "preview", "target_provider": args.target, "before": before, "session_files_to_change": len(changes)}, ensure_ascii=False))
    if not args.apply:
        return 0

    backup_dir = create_backup(changes, root, database_path, backup_root, args.target)
    prepared = prepare_session_updates(changes, args.target)
    replace_sessions(prepared)
    update_threads(database_path, args.target)
    after = verify(root, database_path, args.target, prepared)
    print(json.dumps({"backup_directory": str(backup_dir), "after": after}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
