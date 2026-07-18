"""FLAC metadata read/write via mutagen. Only AlbumArtist is modified on write."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from mutagen.flac import FLAC


def _join_tag(audio: FLAC, key: str) -> str:
    values = audio.get(key)
    if not values:
        return ""
    return "; ".join(str(v) for v in values)


def read_flac_tags(path: str | Path) -> dict[str, Any]:
    """
    Read common tags from a FLAC file.

    Returns dict with path, filename, artist, album, albumartist.
    """
    path = Path(path)
    audio = FLAC(str(path))
    try:
        return {
            "path": str(path),
            "filename": path.name,
            "artist": _join_tag(audio, "artist"),
            "album": _join_tag(audio, "album"),
            "albumartist": _join_tag(audio, "albumartist"),
        }
    finally:
        audio.pictures = []
        del audio


def get_albumartist(path: str | Path) -> str:
    """Return the current albumartist tag (joined if multi-value), or empty string."""
    audio = FLAC(str(path))
    try:
        return _join_tag(audio, "albumartist")
    finally:
        audio.pictures = []
        del audio


def ensure_albumartist(path: str | Path, value: str) -> Literal["updated", "skipped"]:
    """
    Open the FLAC once. Skip if albumartist already matches; otherwise set and save.
    Clears in-memory picture data after use to limit RAM with large embedded art.
    """
    path_str = str(path)
    audio = FLAC(path_str)
    try:
        current = _join_tag(audio, "albumartist").strip()
        if current == value:
            return "skipped"
        audio["albumartist"] = [value]
        audio.save()
        return "updated"
    finally:
        # Drop decoded cover art from this object so GC can reclaim it promptly.
        # Pictures were already written back on save (or never modified on skip).
        try:
            audio.clear_pictures()
        except Exception:  # noqa: BLE001
            audio.pictures = []
        del audio


def set_albumartist(path: str | Path, value: str) -> None:
    """
    Set only the albumartist Vorbis comment on a FLAC file.
    All other metadata is preserved.
    """
    ensure_albumartist(path, value)
