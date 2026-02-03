"""File patching: apply code changes to source files without regeneration."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Optional

from lenspr.models import Node, Patch, PatchError


def apply_patch(file_path: Path, patch: Patch) -> str:
    """
    Apply a single patch to a file.

    Replaces lines start_line:end_line with new_source.
    Returns the new file content.
    """
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    before = lines[: patch.start_line - 1]
    after = lines[patch.end_line :]

    # Ensure new_source ends with newline
    new_source = patch.new_source
    if not new_source.endswith("\n"):
        new_source += "\n"
    new_lines = new_source.splitlines(keepends=True)

    return "".join(before + new_lines + after)


def apply_patches(file_path: Path, patches: list[Patch]) -> str:
    """
    Apply multiple patches to a single file.

    Patches are sorted by start_line DESCENDING (bottom-to-top) so that
    earlier patches don't shift line numbers of later ones.

    Returns the new file content.

    Raises PatchError if patches overlap.
    """
    if not patches:
        return file_path.read_text(encoding="utf-8")

    # Sort bottom-to-top
    sorted_patches = sorted(patches, key=lambda p: p.start_line, reverse=True)

    # Check for overlapping patches
    for i in range(len(sorted_patches) - 1):
        current = sorted_patches[i]
        next_patch = sorted_patches[i + 1]
        if next_patch.end_line >= current.start_line:
            raise PatchError(
                f"Overlapping patches: lines {next_patch.start_line}-{next_patch.end_line} "
                f"and {current.start_line}-{current.end_line}"
            )

    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    for patch in sorted_patches:
        before = lines[: patch.start_line - 1]
        after = lines[patch.end_line :]

        new_source = patch.new_source
        if not new_source.endswith("\n"):
            new_source += "\n"
        new_lines = new_source.splitlines(keepends=True)

        lines = before + new_lines + after

    return "".join(lines)


def compute_line_delta(patch: Patch) -> int:
    """Compute how many lines a patch adds or removes."""
    old_count = patch.end_line - patch.start_line + 1
    new_count = len(patch.new_source.splitlines())
    return new_count - old_count


class PatchBuffer:
    """
    Accumulates patches and applies them in batch.

    When Claude calls lens_update_node multiple times for the same file,
    patches are buffered and applied together on flush() — bottom-to-top
    to avoid line number corruption.
    """

    def __init__(self) -> None:
        self._pending: dict[Path, list[Patch]] = defaultdict(list)

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    @property
    def pending_files(self) -> list[Path]:
        return list(self._pending.keys())

    def add(self, file_path: Path, node: Node, new_source: str) -> None:
        """Buffer a patch for later application."""
        self._pending[file_path].append(
            Patch(
                start_line=node.start_line,
                end_line=node.end_line,
                new_source=new_source,
                node_id=node.id,
            )
        )

    def flush(self, validate: bool = True) -> list[Path]:
        """
        Apply all pending patches to their respective files.

        Args:
            validate: If True, validate syntax of each file before writing.

        Returns:
            List of modified file paths.

        Raises:
            PatchError: If validation fails or patches overlap.
        """
        modified: list[Path] = []

        for file_path, patches in self._pending.items():
            new_content = apply_patches(file_path, patches)

            if validate:
                try:
                    ast.parse(new_content)
                except SyntaxError as e:
                    node_ids = [p.node_id for p in patches]
                    raise PatchError(
                        f"Syntax error after patching {file_path} "
                        f"(nodes: {node_ids}): line {e.lineno}: {e.msg}"
                    ) from e

            file_path.write_text(new_content, encoding="utf-8")
            modified.append(file_path)

        self._pending.clear()
        return modified

    def discard(self, file_path: Optional[Path] = None) -> None:
        """Discard pending patches, optionally for a specific file only."""
        if file_path:
            self._pending.pop(file_path, None)
        else:
            self._pending.clear()


def insert_code(file_path: Path, new_source: str, after_line: int) -> str:
    """
    Insert new code after a specific line.

    Args:
        file_path: Path to the file.
        new_source: Code to insert.
        after_line: Line number after which to insert (0 = beginning of file).

    Returns:
        New file content.
    """
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    new_code = new_source
    if not new_code.endswith("\n"):
        new_code += "\n"

    # Add blank line separators
    insert_lines = ["\n"] + new_code.splitlines(keepends=True) + ["\n"]

    before = lines[:after_line]
    after = lines[after_line:]

    return "".join(before + insert_lines + after)


def remove_lines(file_path: Path, start_line: int, end_line: int) -> str:
    """
    Remove lines from a file.

    Args:
        start_line: First line to remove (1-based, inclusive).
        end_line: Last line to remove (1-based, inclusive).

    Returns:
        New file content.
    """
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    before = lines[: start_line - 1]
    after = lines[end_line:]

    # Clean up excessive blank lines at the join point
    result = before + after
    return "".join(result)
