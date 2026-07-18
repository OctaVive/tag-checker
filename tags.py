"""FLAC metadata read/write via mutagen. Only AlbumArtist is modified on write."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from mutagen.flac import FLAC

# Nonstandard AlbumArtist keys to remove (keep standard "albumartist" only).
_LEGACY_ALBUMARTIST_FORMS = frozenset({"album artist", "album_artist"})


def _join_tag(audio: FLAC, key: str) -> str:
    values = audio.get(key)
    if not values:
        return ""
    return "; ".join(str(v) for v in values)


def _legacy_albumartist_keys(audio: FLAC) -> list[str]:
    """Return tag keys that are spaced/underscore AlbumArtist variants (not albumartist)."""
    found: list[str] = []
    for key in audio.keys():
        lower = key.lower()
        if lower in _LEGACY_ALBUMARTIST_FORMS:
            found.append(key)
    return found


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
    Open the FLAC once. Set standard albumartist; remove legacy ALBUM ARTIST keys.
    Skip only when albumartist already matches and no legacy keys remain.
    """
    path_str = str(path)
    audio = FLAC(path_str)
    try:
        current = _join_tag(audio, "albumartist").strip()
        legacy_keys = _legacy_albumartist_keys(audio)
        if current == value and not legacy_keys:
            return "skipped"

        audio["albumartist"] = [value]
        for key in legacy_keys:
            try:
                del audio[key]
            except KeyError:
                pass
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
    All other metadata is preserved (aside from removing legacy AlbumArtist keys).
    """
    ensure_albumartist(path, value)
