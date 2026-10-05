"""Read-only filesystem scanning."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ScannedFile:
    """Metadata captured for one regular file."""

    path: Path
    size: int
    modified_ns: int
    changed_ns: int
    device: int
    inode: int


def scan_files(root: Path, *, exclude: Iterable[Path] = ()) -> Iterator[ScannedFile]:
    """Yield regular files beneath *root*, omitting explicit paths and symlinks."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    excluded_paths = {path.expanduser().resolve() for path in exclude}

    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        base = Path(directory)
        for filename in filenames:
            path = base / filename
            if path in excluded_paths or path.is_symlink():
                continue
            try:
                stat = path.stat()
            except (FileNotFoundError, PermissionError):
                continue
            if path.is_file():
                yield ScannedFile(
                    path=path,
                    size=stat.st_size,
                    modified_ns=stat.st_mtime_ns,
                    changed_ns=stat.st_ctime_ns,
                    device=stat.st_dev,
                    inode=stat.st_ino,
                )
