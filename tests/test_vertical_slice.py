from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from asset_vfs.cli import database_files, main
from asset_vfs.hashing import sha256_file
from asset_vfs.index import AssetIndex
from asset_vfs.reporting import format_duplicate_report
from asset_vfs.scanner import scan_files


def test_scanner_is_sorted_and_skips_symlinks(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "link.txt").symlink_to(tmp_path / "a.txt")

    scanned = list(scan_files(tmp_path))

    assert [item.path.name for item in scanned] == ["a.txt", "b.txt"]
    assert [item.size for item in scanned] == [1, 1]


def test_scanner_requires_directory(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.touch()
    with pytest.raises(NotADirectoryError):
        list(scan_files(file))


def test_scanner_excludes_database_and_sidecars(tmp_path: Path) -> None:
    database = tmp_path / "index.sqlite"
    excluded = database_files(database)
    (tmp_path / "asset.bin").write_bytes(b"asset")
    for path in excluded:
        path.write_bytes(b"database state")

    scanned = list(scan_files(tmp_path, exclude=excluded))

    assert [item.path.name for item in scanned] == ["asset.bin"]


def test_sha256_file_streams_known_content(tmp_path: Path) -> None:
    file = tmp_path / "asset.bin"
    file.write_bytes(b"abc" * 1000)
    assert sha256_file(file, chunk_size=7) == hashlib.sha256(b"abc" * 1000).hexdigest()


def test_index_and_duplicate_report_are_read_only(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    first = source / "first.bin"
    second = source / "second.bin"
    unique = source / "unique.bin"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    unique.write_bytes(b"different")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()}

    with AssetIndex(tmp_path / "assets.sqlite") as index:
        assert index.index(scan_files(source), scan_root=source) == 3
        groups = index.duplicate_groups()

    assert len(groups) == 1
    assert groups[0].paths == (first, second)
    assert groups[0].size == 4
    report = format_duplicate_report(groups)
    assert str(first) in report and str(second) in report
    assert "unique.bin" not in report
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()}


def test_cli_indexes_and_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "one").write_text("duplicate")
    (source / "two").write_text("duplicate")

    assert main([str(source), "--database", str(tmp_path / "index.sqlite")]) == 0
    output = capsys.readouterr().out
    assert "Indexed 2 files." in output
    assert "2 files" in output


def test_cli_does_not_index_database_inside_scan_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "asset.bin").write_bytes(b"asset")
    database = source / "index.sqlite"

    assert main([str(source), "--database", str(database)]) == 0

    output = capsys.readouterr().out
    assert "Indexed 1 files." in output


def test_repeat_scans_reconcile_deleted_and_moved_paths_by_root(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    deleted = first_root / "deleted.bin"
    moved_from = first_root / "before.bin"
    retained = second_root / "retained.bin"
    deleted.write_bytes(b"delete me")
    moved_from.write_bytes(b"move me")
    retained.write_bytes(b"keep me")

    with AssetIndex(tmp_path / "assets.sqlite") as index:
        index.index(scan_files(first_root), scan_root=first_root)
        index.index(scan_files(second_root), scan_root=second_root)

        deleted.unlink()
        moved_to = first_root / "after.bin"
        moved_from.rename(moved_to)
        index.index(scan_files(first_root), scan_root=first_root)

        indexed_paths = {
            Path(path) for (path,) in index.connection.execute("SELECT path FROM files")
        }

    assert indexed_paths == {moved_to, retained}


def test_repeat_scan_reuses_digest_until_metadata_changes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    asset = source / "asset.bin"
    asset.write_bytes(b"original")

    with AssetIndex(tmp_path / "assets.sqlite") as index:
        index.index(scan_files(source), scan_root=source)

        with patch(
            "asset_vfs.index.sha256_file",
            side_effect=AssertionError("unchanged file was read"),
        ):
            index.index(scan_files(source), scan_root=source)

        asset.write_bytes(b"changed and longer")
        with patch("asset_vfs.index.sha256_file", wraps=sha256_file) as hasher:
            index.index(scan_files(source), scan_root=source)

        digest = index.connection.execute(
            "SELECT sha256 FROM files WHERE path = ?", (str(asset),)
        ).fetchone()[0]

    hasher.assert_called_once_with(asset)
    assert digest == hashlib.sha256(b"changed and longer").hexdigest()


def test_existing_index_schema_gains_cache_metadata(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE files (
                   path TEXT PRIMARY KEY,
                   size INTEGER NOT NULL,
                   modified_ns INTEGER NOT NULL,
                   sha256 TEXT NOT NULL
               )"""
        )

    with AssetIndex(database) as index:
        columns = {
            name
            for _, name, *_ in index.connection.execute("PRAGMA table_info(files)")
        }

    assert {"changed_ns", "device", "inode"} <= columns
