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
    changed_ns INTEGER NOT NULL,
    device INTEGER NOT NULL,
    inode INTEGER NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
);
CREATE INDEX IF NOT EXISTS files_sha256_idx ON files (sha256);
CREATE TABLE IF NOT EXISTS scan_roots (
    path TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS scan_memberships (
    scan_root TEXT NOT NULL REFERENCES scan_roots(path) ON DELETE CASCADE,
    file_path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    PRIMARY KEY (scan_root, file_path)
);
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
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self._migrate_file_metadata()

    def __enter__(self) -> AssetIndex:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def index(self, files: Iterable[ScannedFile], *, scan_root: Path) -> int:
        """Replace one root's index membership and remove its stale records."""
        scan_root = scan_root.expanduser().resolve()
        root_text = str(scan_root)
        count = 0
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO scan_roots (path) VALUES (?)", (root_text,)
            )
            self._seed_legacy_memberships(scan_root)
            previous_paths = {
                path
                for (path,) in self.connection.execute(
                    "SELECT file_path FROM scan_memberships WHERE scan_root = ?",
                    (root_text,),
                )
            }
            self.connection.execute(
                "DELETE FROM scan_memberships WHERE scan_root = ?", (root_text,)
            )

            seen_paths: set[str] = set()
            for file in files:
                path = file.path.expanduser().resolve()
                if not path.is_relative_to(scan_root):
                    raise ValueError(f"indexed path is outside scan root: {path}")
                path_text = str(path)
                metadata = (
                    file.size,
                    file.modified_ns,
                    file.changed_ns,
                    file.device,
                    file.inode,
                )
                existing = self.connection.execute(
                    """SELECT size, modified_ns, changed_ns, device, inode, sha256
                       FROM files WHERE path = ?""",
                    (path_text,),
                ).fetchone()
                digest = (
                    existing[5]
                    if existing is not None and existing[:5] == metadata
                    else sha256_file(path)
                )
                self.connection.execute(
                    """INSERT INTO files
                         (path, size, modified_ns, changed_ns, device, inode, sha256)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(path) DO UPDATE SET
                         size = excluded.size,
                         modified_ns = excluded.modified_ns,
                         changed_ns = excluded.changed_ns,
                         device = excluded.device,
                         inode = excluded.inode,
                         sha256 = excluded.sha256""",
                    (path_text, *metadata, digest),
                )
                self.connection.execute(
                    """INSERT INTO scan_memberships (scan_root, file_path)
                       VALUES (?, ?)""",
                    (root_text, path_text),
                )
                seen_paths.add(path_text)
                count += 1

            for stale_path in previous_paths - seen_paths:
                self.connection.execute(
                    """DELETE FROM files
                       WHERE path = ?
                         AND NOT EXISTS (
                             SELECT 1 FROM scan_memberships
                             WHERE file_path = files.path
                         )""",
                    (stale_path,),
                )
        return count

    def _migrate_file_metadata(self) -> None:
        """Add cache-validation metadata to indexes created by older versions."""
        columns = {
            name
            for _, name, *_ in self.connection.execute("PRAGMA table_info(files)")
        }
        for name in ("changed_ns", "device", "inode"):
            if name not in columns:
                self.connection.execute(
                    f"ALTER TABLE files ADD COLUMN {name} INTEGER NOT NULL DEFAULT -1"
                )

    def _seed_legacy_memberships(self, scan_root: Path) -> None:
        """Associate pre-scope index rows without discarding unrelated history."""
        root_text = str(scan_root)
        for (path_text,) in self.connection.execute("SELECT path FROM files"):
            if Path(path_text).is_relative_to(scan_root):
                self.connection.execute(
                    """INSERT OR IGNORE INTO scan_memberships (scan_root, file_path)
                       VALUES (?, ?)""",
                    (root_text, path_text),
                )

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
