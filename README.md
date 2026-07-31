# asset-vfs

Local macOS asset deduplication and virtual filesystem MVP.

## Purpose

`asset-vfs` explores a local-first architecture for identifying duplicate assets by SHA-256, indexing metadata in SQLite, consolidating duplicate storage, and exposing a virtual filesystem view on macOS through MacFUSE.

## Architecture

- **Content identity:** SHA-256 hashes identify byte-identical files.
- **Metadata/index:** SQLite stores asset records, paths, hashes, sizes, and deduplication state.
- **Storage:** local filesystem remains the source for file bytes during the MVP.
- **Deduplication:** hard links may be used only where filesystem constraints make them safe and appropriate.
- **Virtual view:** MacFUSE provides a mount layer that can present logical paths independently of physical storage.
- **GitHub:** source of truth for code, documentation, issues, pull requests, and CI.

## Runtime boundary

The live filesystem, SQLite database, MacFUSE installation, mount process, and integration tests run on a macOS host. They are not hosted by GitHub or ChatGPT.

## MVP sequence

1. Scan a configured directory without modifying files.
2. Hash regular files with SHA-256.
3. Persist file and hash metadata in SQLite.
4. Report duplicate groups.
5. Add an explicit, reversible deduplication operation.
6. Expose indexed assets through a read-only MacFUSE mount.
7. Add tests for hashing, indexing, deduplication safety, and virtual-path behavior.

## Development

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

The repository is intentionally minimal at initialization. Implementation should proceed in small, testable increments, with destructive filesystem behavior disabled by default.
