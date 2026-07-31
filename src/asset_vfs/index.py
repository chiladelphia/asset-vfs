"""SQLite-backed asset index."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .hashing import sha256_file
from .scanner import ScannedFile


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL CHECK (size >= 0),
    modified_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
);
CREATE INDEX IF NOT EXISTS files_sha256_idx ON files (sha256);
"""


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    sha256: str
    size: int
    paths: tuple[Path, ...]


class AssetIndex:
    """Persist scan metadata; the indexed source files are never modified."""

    def __init__(self, database: Path) -> None:
        self.database = database
        self.connection = sqlite3.connect(database)
        self.connection.executescript(SCHEMA)

    def __enter__(self) -> AssetIndex:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def index(self, files: Iterable[ScannedFile]) -> int:
        """Hash and upsert scanned files, returning the indexed file count."""
        count = 0
        with self.connection:
            for file in files:
                digest = sha256_file(file.path)
                self.connection.execute(
                    """INSERT INTO files (path, size, modified_ns, sha256)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(path) DO UPDATE SET
                         size = excluded.size,
                         modified_ns = excluded.modified_ns,
                         sha256 = excluded.sha256""",
                    (str(file.path), file.size, file.modified_ns, digest),
                )
                count += 1
        return count

    def duplicate_groups(self) -> list[DuplicateGroup]:
        """Return byte-identical groups in stable order."""
        rows = self.connection.execute(
            """SELECT sha256, size, path
               FROM files
               WHERE sha256 IN (
                   SELECT sha256 FROM files GROUP BY sha256 HAVING count(*) > 1
               )
               ORDER BY sha256, path"""
        )
        grouped: dict[tuple[str, int], list[Path]] = {}
        for digest, size, path in rows:
            grouped.setdefault((digest, size), []).append(Path(path))
        return [
            DuplicateGroup(digest, size, tuple(paths))
            for (digest, size), paths in grouped.items()
        ]
