"""Folder tree building and FLAC file discovery."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def get_music_root() -> Path:
    """Return MUSIC_PATH (default /music), validating it exists and is a directory."""
    root = Path(os.environ.get("MUSIC_PATH", "/music")).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Music root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Music root is not a directory: {root}")
    return root


def is_under_root(path: Path, root: Path | None = None) -> bool:
    """Return True if path resolves under the music root."""
    if root is None:
        root = get_music_root()
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_selected_dirs(selected_dirs: list[str], root: Path | None = None) -> list[Path]:
    """Validate and resolve selected folder paths; reject escapes outside MUSIC_PATH."""
    if root is None:
        root = get_music_root()
    root = root.resolve()
    validated: list[Path] = []
    for raw in selected_dirs:
        if not raw or not isinstance(raw, str):
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if not is_under_root(candidate, root) and candidate != root:
            raise ValueError(f"Path is outside music root: {raw}")
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError(f"Not a directory: {raw}")
        validated.append(candidate)
    return validated


def build_folder_tree(root: Path | None = None) -> dict[str, Any]:
    """
    Build a recursive folder-only tree rooted at MUSIC_PATH.

    Returns a dict:
      { "name": str, "path": str, "children": [ ... ] }
    """
    if root is None:
        root = get_music_root()
    root = root.resolve()

    def _walk(directory: Path) -> dict[str, Any]:
        children: list[dict[str, Any]] = []
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except PermissionError:
            entries = []
        for entry in entries:
            if entry.is_dir() and not entry.name.startswith("."):
                children.append(_walk(entry))
        return {
            "name": directory.name if directory != root else root.name,
            "path": str(directory),
            "children": children,
        }

    return _walk(root)


def find_flac_files(selected_dirs: list[str], root: Path | None = None) -> list[str]:
    """
    Recursively find every .flac / .FLAC under each selected folder using os.walk().
    Deduplicates paths. Rejects selections outside MUSIC_PATH.
    """
    return sorted(set(iter_flac_files(selected_dirs, root)))


def iter_flac_files(selected_dirs: list[str], root: Path | None = None):
    """
    Yield FLAC paths under selected folders via os.walk(), without buffering all paths.
    Deduplicates across overlapping selections. Rejects paths outside MUSIC_PATH.
    """
    if root is None:
        root = get_music_root()
    dirs = validate_selected_dirs(selected_dirs, root)
    seen: set[str] = set()
    for directory in dirs:
        for dirpath, _dirnames, filenames in os.walk(directory):
            for name in filenames:
                if name.lower().endswith(".flac"):
                    full = str(Path(dirpath) / name)
                    if full not in seen:
                        seen.add(full)
                        yield full
