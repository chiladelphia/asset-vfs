"""Command-line entry point for the read-only indexing slice."""

from __future__ import annotations

import argparse
from pathlib import Path

from .index import AssetIndex
from .reporting import format_duplicate_report
from .scanner import scan_files


def database_files(database: Path) -> tuple[Path, Path, Path]:
    """Return the SQLite database path and its possible WAL sidecars."""
    database = database.expanduser().resolve()
    return database, Path(f"{database}-wal"), Path(f"{database}-shm")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index and report duplicate files")
    parser.add_argument("root", type=Path, help="directory to scan")
    parser.add_argument("--database", type=Path, required=True, help="SQLite index path")
    args = parser.parse_args(argv)

    excluded = database_files(args.database)
    with AssetIndex(excluded[0]) as index:
        count = index.index(scan_files(args.root, exclude=excluded))
        print(f"Indexed {count} files.")
        print(format_duplicate_report(index.duplicate_groups()))
    return 0
