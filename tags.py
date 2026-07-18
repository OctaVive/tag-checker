"""FLAC metadata read/write via mutagen. Only AlbumArtist is modified on write."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    return {
        "path": str(path),
        "filename": path.name,
        "artist": _join_tag(audio, "artist"),
        "album": _join_tag(audio, "album"),
        "albumartist": _join_tag(audio, "albumartist"),
    }


def set_albumartist(path: str | Path, value: str) -> None:
    """
    Set only the albumartist Vorbis comment on a FLAC file.
    All other metadata is preserved.
    """
    path = Path(path)
    audio = FLAC(str(path))
    audio["albumartist"] = [value]
    audio.save()
