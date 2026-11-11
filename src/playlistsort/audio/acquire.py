"""Find audio for each track: the user's own files first, 30 s previews second.

Local files come from a rekordbox XML export and/or folders (tags via mutagen, filename as a
fallback). Matching is ISRC first, then the same remix-aware fuzzy matcher the metadata
sources use. Previews are downloaded from Deezer into `data/audio/` because the URLs expire;
only the local path is stored.
"""

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from rapidfuzz import fuzz, process

from playlistsort import db
from playlistsort.enrich.base import TrackRow, load_tracks, query_for
from playlistsort.enrich.deezer import DeezerEnricher
from playlistsort.enrich.match import Candidate, pick_best

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".aiff", ".aif", ".wav", ".flac", ".ogg", ".opus"}
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|;|\bfeat\.?\b|\bft\.?\b|\bx\b)\s*", re.IGNORECASE)
_FILENAME = re.compile(r"^(?:\d+[\s.\-_]+)?(?P<artist>.+?)\s+-\s+(?P<title>.+)$")


@dataclass(frozen=True)
class LocalFile:
    path: Path
    title: str | None
    artist: str | None
    album: str | None
    duration_s: float | None
    isrc: str | None
    bpm: float | None
    key: str | None
    source: str  # rekordbox | folder


@dataclass(frozen=True)
class LocalMatch:
    track_id: str
    file: LocalFile
    method: str  # isrc | fuzzy | fuzzy_ambiguous
    score: float


# --------------------------------------------------------------------------- discovery


def _attr_float(el: ET.Element, name: str) -> float | None:
    value = el.get(name)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_rekordbox_xml(path: str | Path) -> list[LocalFile]:
    out: list[LocalFile] = []
    for _event, el in ET.iterparse(str(path), events=("end",)):
        if el.tag != "TRACK" or not el.get("Location"):
            continue
        loc = urlparse(el.get("Location", ""))
        file_path = Path(unquote(loc.path))
        out.append(
            LocalFile(
                path=file_path,
                title=el.get("Name") or None,
                artist=el.get("Artist") or None,
                album=el.get("Album") or None,
                duration_s=_attr_float(el, "TotalTime"),
                isrc=None,
                bpm=_attr_float(el, "AverageBpm"),
                key=el.get("Tonality") or None,
                source="rekordbox",
            )
        )
        el.clear()
    return out


def _from_filename(path: Path) -> tuple[str | None, str | None]:
    m = _FILENAME.match(path.stem)
    if m:
        return m.group("artist").strip(), m.group("title").strip()
    return None, path.stem


def _first(tags, *keys: str) -> str | None:
    if tags is None:
        return None
    for key in keys:
        try:
            value = tags.get(key)
        except (KeyError, ValueError, TypeError):
            value = None
        if value:
            if isinstance(value, list | tuple):
                value = value[0]
            text = getattr(value, "text", value)
            if isinstance(text, list | tuple):
                text = text[0] if text else None
            if text:
                return str(text).strip()
    return None


def read_file_tags(path: Path) -> LocalFile:
    import mutagen

    title = artist = album = isrc = None
    duration = None
    try:
        audio = mutagen.File(str(path), easy=True)
    except Exception:  # noqa: BLE001 - unreadable file: fall back to the filename
        audio = None
    if audio is not None:
        info = getattr(audio, "info", None)
        duration = (
            float(info.length) if info is not None and getattr(info, "length", None) else None
        )
        tags = getattr(audio, "tags", None)
        title = _first(tags, "title", "TIT2", "\xa9nam")
        artist = _first(tags, "artist", "TPE1", "\xa9ART")
        album = _first(tags, "album", "TALB", "\xa9alb")
        isrc = _first(tags, "isrc", "TSRC")
        if not title or not artist:
            try:  # easy tags missed; try the raw frames (WAV/ID3 etc.)
                raw = mutagen.File(str(path))
                rtags = getattr(raw, "tags", None)
                title = title or _first(rtags, "TIT2", "\xa9nam", "TITLE")
                artist = artist or _first(rtags, "TPE1", "\xa9ART", "ARTIST")
                album = album or _first(rtags, "TALB", "\xa9alb", "ALBUM")
                isrc = isrc or _first(rtags, "TSRC", "ISRC")
            except Exception:  # noqa: BLE001
                pass
    if not title:
        fn_artist, fn_title = _from_filename(path)
        title = fn_title
        artist = artist or fn_artist
    return LocalFile(
        path=path,
        title=title,
        artist=artist,
        album=album,
        duration_s=duration,
        isrc=isrc.upper().replace("-", "") if isrc else None,
        bpm=None,
        key=None,
        source="folder",
    )


def scan_folders(dirs: Iterable[str | Path]) -> list[LocalFile]:
    out: list[LocalFile] = []
    for d in dirs:
        root = Path(d).expanduser()
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith("."):
                out.append(read_file_tags(p))
    return out


# --------------------------------------------------------------------------- matching


def _artists(text: str | None) -> tuple[str, ...]:
    return tuple(a.strip() for a in _ARTIST_SPLIT.split(text or "") if a.strip())


def _candidate(f: LocalFile) -> Candidate:
    return Candidate(
        id=str(f.path),
        title=f.title or f.path.stem,
        mix_name=None,
        artists=_artists(f.artist),
        remixers=(),
        duration_ms=int(f.duration_s * 1000) if f.duration_s else None,
        isrc=f.isrc,
    )


def match_local(
    tracks: Sequence[TrackRow], files: Sequence[LocalFile], *, shortlist: int = 15
) -> list[LocalMatch]:
    by_isrc: dict[str, LocalFile] = {f.isrc: f for f in files if f.isrc}
    candidates = [_candidate(f) for f in files]
    by_id = {c.id: f for c, f in zip(candidates, files, strict=True)}
    titles = [c.title for c in candidates]
    out: list[LocalMatch] = []
    for track in tracks:
        if track.isrc and track.isrc in by_isrc:
            out.append(LocalMatch(track.spotify_id, by_isrc[track.isrc], "isrc", 1.0))
            continue
        if not candidates:
            continue
        hits = process.extract(
            track.base_title, titles, scorer=fuzz.token_set_ratio, limit=shortlist
        )
        pool = [candidates[idx] for _title, _score, idx in hits]
        best = pick_best(query_for(track), pool)
        if best:
            out.append(
                LocalMatch(track.spotify_id, by_id[best.candidate.id], best.method, best.score)
            )
    return out


def register_matches(conn: sqlite3.Connection, matches: Iterable[LocalMatch]) -> int:
    n = 0
    with conn:
        for m in matches:
            db.upsert_audio_source(
                conn,
                track_id=m.track_id,
                kind="local",
                local_path=str(m.file.path),
                duration_s=m.file.duration_s,
            )
            n += 1
    return n


# --------------------------------------------------------------------------- previews


@dataclass
class PreviewStats:
    downloaded: int = 0
    already: int = 0
    skipped_no_preview: int = 0
    failed: int = 0


def fetch_previews(
    conn: sqlite3.Connection,
    deezer: DeezerEnricher,
    dest_dir: str | Path,
    *,
    limit: int | None = None,
    downloader: httpx.Client | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> PreviewStats:
    """Download a 30 s Deezer preview for every track Deezer said has one."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    client = downloader or httpx.Client(timeout=30, follow_redirects=True)
    flagged = {
        r["track_id"]
        for r in conn.execute(
            "select track_id from evidence where source='deezer' and field='preview_available' and raw_value='true'"
        )
    }
    have = {
        r["track_id"]
        for r in conn.execute(
            "select track_id from audio_sources where kind in ('local','deezer_preview')"
        )
    }
    stats = PreviewStats()
    todo = [t for t in load_tracks(conn) if t.isrc and t.spotify_id in flagged]
    if limit is not None:
        todo = todo[:limit]
    for i, track in enumerate(todo, 1):
        if track.spotify_id in have:
            stats.already += 1
            continue
        url = deezer.preview_url(track.isrc or "")
        if not url:
            stats.skipped_no_preview += 1
            continue
        target = dest / f"{track.spotify_id}.mp3"
        try:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with target.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
        except httpx.HTTPError:
            stats.failed += 1
            target.unlink(missing_ok=True)
            continue
        with conn:
            db.upsert_audio_source(
                conn,
                track_id=track.spotify_id,
                kind="deezer_preview",
                local_path=str(target),
                duration_s=30.0,
            )
        stats.downloaded += 1
        if progress:
            progress(i, len(todo))
    return stats
