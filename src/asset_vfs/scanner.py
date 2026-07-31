"""Read-only filesystem scanning."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ScannedFile:
    """Metadata captured for one regular file."""

    path: Path
    size: int
    modified_ns: int


def scan_files(root: Path) -> Iterator[ScannedFile]:
    """Yield regular files beneath *root* without following symlinks."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)

    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        base = Path(directory)
        for filename in filenames:
            path = base / filename
            if path.is_symlink():
                continue
            try:
                stat = path.stat()
            except (FileNotFoundError, PermissionError):
                continue
            if path.is_file():
                yield ScannedFile(path=path, size=stat.st_size, modified_ns=stat.st_mtime_ns)
