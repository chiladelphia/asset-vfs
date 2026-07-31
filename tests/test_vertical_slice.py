from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from asset_vfs.cli import main
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
        assert index.index(scan_files(source)) == 3
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
