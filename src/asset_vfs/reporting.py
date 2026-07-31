"""Duplicate report formatting."""

from __future__ import annotations

from collections.abc import Iterable

from .index import DuplicateGroup


def format_duplicate_report(groups: Iterable[DuplicateGroup]) -> str:
    """Format duplicate groups as a deterministic plain-text report."""
    groups = list(groups)
    if not groups:
        return "No duplicates found."

    sections = []
    for group in groups:
        heading = f"{group.sha256} ({group.size} bytes, {len(group.paths)} files)"
        sections.append("\n".join([heading, *(f"  {path}" for path in group.paths)]))
    return "\n\n".join(sections)
