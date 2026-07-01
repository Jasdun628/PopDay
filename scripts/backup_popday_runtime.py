#!/usr/bin/env python3
"""Create a timestamped PopDay runtime/source backup on the Mac Mini."""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import subprocess
from pathlib import Path


DEFAULT_RUNTIME_DIR = Path("/Users/jasondunne/PopDayRuntime")
DEFAULT_SOURCE_REPO = Path("/Users/jasondunne/Documents/PopDay")
DEFAULT_BACKUP_ROOT = Path("/Users/jasondunne/PopDayBackups")
DEFAULT_KEEP = 30


def _timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _copy_file(src: Path, dst: Path, copied: list[tuple[Path, Path, int]]) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied.append((src, dst, dst.stat().st_size))


def _ignore_source(dirpath: str, names: list[str]) -> set[str]:
    ignored = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
    return ignored.intersection(names)


def _copy_tree(src: Path, dst: Path, copied: list[tuple[Path, Path, int]]) -> None:
    if not src.exists():
        return
    shutil.copytree(src, dst, ignore=_ignore_source)
    total = sum(path.stat().st_size for path in dst.rglob("*") if path.is_file())
    copied.append((src, dst, total))


def _sqlite_counts(db_path: Path) -> list[str]:
    if not db_path.exists():
        return [f"{db_path}: missing"]
    tables = [
        "processed_filings",
        "detections",
        "known_announcements",
        "price_reactions",
        "alert_recipients",
        "rules",
    ]
    rows: list[str] = []
    con = sqlite3.connect(db_path)
    try:
        for table in tables:
            try:
                count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            except sqlite3.Error as exc:
                rows.append(f"{table}: error {exc}")
            else:
                rows.append(f"{table}: {count}")
    finally:
        con.close()
    return rows


def _git_commit(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception as exc:
        return f"unknown: {exc}"


def _prune_backups(root: Path, keep: int) -> list[Path]:
    backups = sorted(path for path in root.iterdir() if path.is_dir())
    pruned: list[Path] = []
    for backup in backups[:-keep]:
        shutil.rmtree(backup)
        pruned.append(backup)
    return pruned


def create_backup(
    *,
    reason: str,
    runtime_dir: Path,
    source_repo: Path,
    backup_root: Path,
    keep: int,
) -> Path:
    stamp = _timestamp()
    target = backup_root / stamp
    target.mkdir(parents=True, exist_ok=False)

    copied: list[tuple[Path, Path, int]] = []
    db_path = runtime_dir / "popday.sqlite3"

    _copy_file(db_path, target / "popday.sqlite3", copied)
    _copy_file(runtime_dir / "config.json", target / "config.json", copied)
    _copy_tree(runtime_dir, target / "PopDayRuntime", copied)
    _copy_tree(source_repo, target / "PopDaySource", copied)

    manifest = [
        f"timestamp: {stamp}",
        f"reason: {reason}",
        f"git_commit: {_git_commit(source_repo)}",
        "",
        "sqlite_row_counts:",
        *[f"  {line}" for line in _sqlite_counts(db_path)],
        "",
        "files_copied:",
    ]
    for src, dst, size in copied:
        manifest.extend(
            [
                f"  source: {src}",
                f"  backup: {dst}",
                f"  size_bytes: {size}",
            ]
        )

    pruned = _prune_backups(backup_root, keep)
    manifest.append("")
    manifest.append(f"retention_keep: {keep}")
    if pruned:
        manifest.append("pruned_backups:")
        manifest.extend(f"  {path}" for path in pruned)
    else:
        manifest.append("pruned_backups: none")

    (target / "manifest.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a timestamped PopDay backup.")
    parser.add_argument("--reason", required=True, help="Plain-English reason for the backup.")
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--source-repo", type=Path, default=DEFAULT_SOURCE_REPO)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    backup = create_backup(
        reason=args.reason,
        runtime_dir=args.runtime_dir,
        source_repo=args.source_repo,
        backup_root=args.backup_root,
        keep=args.keep,
    )
    print(f"PopDay backup created: {backup}")
    print(f"Manifest: {backup / 'manifest.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
