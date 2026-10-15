"""Read Liked Songs into the local library.

`parse_saved_item` is pure and tested; `ingest` pages `GET /me/tracks` (50 per call) and
writes tracks, artists and albums. Album labels need one `GET /albums/{id}` each (bulk
endpoints are gone in Development Mode), so `fetch_album_labels` runs separately and only
for albums we have not looked up yet.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import spotipy

from playlistsort import db
from playlistsort.titles import ParsedTitle, parse_title


@dataclass(frozen=True)
class TrackRecord:
    spotify_id: str
    uri: str
    title: str
    isrc: str | None
    duration_ms: int | None
    explicit: bool
    is_local: bool
    album_id: str | None
    album_name: str | None
    album_release_date: str | None
    artists: tuple[tuple[str | None, str], ...]
    added_at: str | None
    parsed: ParsedTitle
    raw: dict[str, Any]


def parse_saved_item(item: dict[str, Any]) -> TrackRecord | None:
    track = item.get("track")
    if not track:
        return None
    uri = track.get("uri") or ""
    is_local = bool(track.get("is_local")) or uri.startswith("spotify:local:")
    spotify_id = track.get("id") or uri
    if not spotify_id:
        return None
    album = track.get("album") or {}
    artists = tuple((a.get("id"), a.get("name") or "") for a in track.get("artists") or [])
    title = track.get("name") or ""
    parsed = parse_title(title, [name for _, name in artists])
    external_ids = track.get("external_ids") or {}
    return TrackRecord(
        spotify_id=spotify_id,
        uri=uri,
        title=title,
        isrc=external_ids.get("isrc") or None,
        duration_ms=track.get("duration_ms"),
        explicit=bool(track.get("explicit")),
        is_local=is_local,
        album_id=album.get("id") or None,
        album_name=album.get("name") or None,
        album_release_date=album.get("release_date") or None,
        artists=artists,
        added_at=item.get("added_at"),
        parsed=parsed,
        raw=track,
    )


def iter_saved_tracks(
    sp: spotipy.Spotify, *, limit: int | None = None, page_size: int = 50
) -> Iterator[tuple[dict[str, Any], int | None]]:
    """Yield (item, total) for every saved track, newest first, paging by offset."""
    offset = 0
    seen = 0
    while True:
        page = sp.current_user_saved_tracks(limit=page_size, offset=offset)
        items = page.get("items") or []
        total = page.get("total")
        if not items:
            return
        for item in items:
            yield item, total
            seen += 1
            if limit is not None and seen >= limit:
                return
        offset += len(items)
        if page.get("next") is None:
            return


@dataclass
class IngestStats:
    tracks: int = 0
    local: int = 0
    artists: int = 0
    albums: int = 0
    total_reported: int | None = None


def _store(conn: sqlite3.Connection, rec: TrackRecord) -> None:
    db.upsert_track(
        conn,
        spotify_id=rec.spotify_id,
        title=rec.title,
        isrc=rec.isrc,
        duration_ms=rec.duration_ms,
        explicit=rec.explicit,
        is_local=rec.is_local,
        album_id=rec.album_id,
        added_at=rec.added_at,
        base_title=rec.parsed.base_title,
        mix_name=rec.parsed.mix_name,
        mix_type=rec.parsed.mix_type,
        remixers=rec.parsed.remixers,
        is_remix=rec.parsed.is_remix,
        title_confidence=rec.parsed.confidence,
        raw={
            "artists": [{"id": i, "name": n} for i, n in rec.artists],
            "album": {"id": rec.album_id, "name": rec.album_name},
            "featuring": list(rec.parsed.featuring),
        },
    )
    for position, (artist_id, name) in enumerate(rec.artists):
        if not artist_id:
            continue
        db.upsert_artist(conn, spotify_id=artist_id, name=name)
        db.link_track_artist(conn, track_id=rec.spotify_id, artist_id=artist_id, position=position)
    if rec.album_id:
        db.upsert_album(
            conn, spotify_id=rec.album_id, name=rec.album_name, release_date=rec.album_release_date
        )


def ingest(
    sp: spotipy.Spotify,
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    progress: Callable[[int, int | None], None] | None = None,
) -> IngestStats:
    stats = IngestStats()
    artist_ids: set[str] = set()
    album_ids: set[str] = set()
    with conn:
        for item, total in iter_saved_tracks(sp, limit=limit):
            stats.total_reported = total
            rec = parse_saved_item(item)
            if rec is None:
                continue
            _store(conn, rec)
            stats.tracks += 1
            stats.local += int(rec.is_local)
            artist_ids.update(a for a, _ in rec.artists if a)
            if rec.album_id:
                album_ids.add(rec.album_id)
            if progress:
                progress(stats.tracks, total)
    stats.artists = len(artist_ids)
    stats.albums = len(album_ids)
    return stats


def fetch_album_labels(
    sp: spotipy.Spotify,
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    pause: float = 0.05,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Fill `albums.label` one album at a time. Returns the number of albums fetched."""
    rows = conn.execute(
        "select spotify_id from albums where label is null order by spotify_id"
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    done = 0
    for row in rows:
        data = sp.album(row["spotify_id"])
        with conn:
            db.upsert_album(
                conn,
                spotify_id=row["spotify_id"],
                name=data.get("name"),
                label=data.get("label") or "",
                release_date=data.get("release_date"),
            )
        done += 1
        if progress:
            progress(done, len(rows))
        if pause:
            time.sleep(pause)
    return done


def fetch_artist_genres(
    sp: spotipy.Spotify,
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    pause: float = 0.05,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Fill `artists.genres_json` (sparse since 2025, kept as a weak prior)."""
    rows = conn.execute(
        "select spotify_id, name from artists where genres_json is null order by spotify_id"
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    done = 0
    for row in rows:
        data = sp.artist(row["spotify_id"])
        with conn:
            db.upsert_artist(
                conn,
                spotify_id=row["spotify_id"],
                name=data.get("name") or row["name"],
                genres=list(data.get("genres") or []),
            )
        done += 1
        if progress:
            progress(done, len(rows))
        if pause:
            time.sleep(pause)
    return done
