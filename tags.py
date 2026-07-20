"""FLAC metadata read/write for Various Artists compilations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from mutagen.flac import FLAC

VA_ALBUMARTIST = "Various Artists"
COMPILATION_VALUE = "1"

# Nonstandard AlbumArtist keys to remove (keep standard "albumartist" only).
_LEGACY_ALBUMARTIST_FORMS = frozenset({"album artist", "album_artist"})
_MB_ALBUM_TAGS_TO_REMOVE = frozenset(
    {
        "musicbrainz_albumid",
        "musicbrainz_albumartistid",
        "musicbrainz_releasegroupid",
        "musicbrainz_releasetrackid",
        "musicbrainz_albumstatus",
        "musicbrainz_albumtype",
        "releasestatus",
        "releasetype",
    }
)


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


def _removable_album_keys(audio: FLAC) -> list[str]:
    """Return album-level MusicBrainz/release keys that should be removed."""
    found: list[str] = []
    for key in audio.keys():
        if key.lower() in _MB_ALBUM_TAGS_TO_REMOVE:
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


def ensure_va_compilation(
    path: str | Path,
    *,
    renumber: tuple[int, int] | None = None,
) -> Literal["updated", "skipped"]:
    """
    Normalize a FLAC for a Various Artists compilation.

    Sets standard albumartist and COMPILATION=1, removes legacy AlbumArtist keys,
    strips album-level MusicBrainz/release tags that split Navidrome albums, and
    optionally rewrites track/disc numbering for folder-based compilations.
    """
    path_str = str(path)
    audio = FLAC(path_str)
    try:
        current = _join_tag(audio, "albumartist").strip()
        compilation = _join_tag(audio, "compilation").strip()
        legacy_keys = _legacy_albumartist_keys(audio)
        removable_keys = _removable_album_keys(audio)

        needs_renumber = False
        track_number = None
        track_total = None
        if renumber is not None:
            track_number, track_total = renumber
            current_track = _join_tag(audio, "tracknumber").strip()
            current_track_total = _join_tag(audio, "tracktotal").strip() or _join_tag(audio, "totaltracks").strip()
            current_disc = _join_tag(audio, "discnumber").strip()
            current_disc_total = _join_tag(audio, "disctotal").strip() or _join_tag(audio, "totaldiscs").strip()
            needs_renumber = any(
                (
                    current_track != str(track_number),
                    current_track_total != str(track_total),
                    current_disc != "1",
                    current_disc_total != "1",
                )
            )

        if current == VA_ALBUMARTIST and compilation == COMPILATION_VALUE and not legacy_keys and not removable_keys and not needs_renumber:
            return "skipped"

        audio["albumartist"] = [VA_ALBUMARTIST]
        audio["compilation"] = [COMPILATION_VALUE]
        for key in legacy_keys:
            try:
                del audio[key]
            except KeyError:
                pass
        for key in removable_keys:
            try:
                del audio[key]
            except KeyError:
                pass
        if renumber is not None and track_number is not None and track_total is not None:
            audio["tracknumber"] = [str(track_number)]
            audio["tracktotal"] = [str(track_total)]
            audio["discnumber"] = ["1"]
            audio["disctotal"] = ["1"]
            for key in ("totaltracks", "totaldiscs"):
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
    path_str = str(path)
    audio = FLAC(path_str)
    try:
        audio["albumartist"] = [value]
        for key in _legacy_albumartist_keys(audio):
            try:
                del audio[key]
            except KeyError:
                pass
        audio.save()
    finally:
        try:
            audio.clear_pictures()
        except Exception:  # noqa: BLE001
            audio.pictures = []
        del audio


def ensure_albumartist(path: str | Path, value: str) -> Literal["updated", "skipped"]:
    """
    Backward-compatible wrapper for callers that only want AlbumArtist normalized.
    """
    current = get_albumartist(path).strip()
    if current == value:
        audio = FLAC(str(path))
        try:
            if not _legacy_albumartist_keys(audio):
                return "skipped"
        finally:
            try:
                audio.clear_pictures()
            except Exception:  # noqa: BLE001
                audio.pictures = []
            del audio
    set_albumartist(path, value)
    return "updated"
