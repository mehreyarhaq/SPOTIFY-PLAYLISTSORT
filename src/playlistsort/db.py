"""SQLite storage: connection, migrations and small repository helpers.

Helpers do not commit; callers wrap work in `with conn:` so bulk operations stay fast and
atomic. `migrate` is the exception and commits each migration as it applies.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).parent / "db_migrations"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


# --------------------------------------------------------------------------- connection


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    target = str(path)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    if target != ":memory:":
        conn.execute("pragma journal_mode = wal")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("select max(version) from schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every `NNN_name.sql` in MIGRATIONS_DIR newer than the current version."""
    current = schema_version(conn)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version <= current:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        with conn:
            conn.execute(
                "insert into schema_version(version, applied_at) values (?, ?)",
                (version, _iso(_now())),
            )
        current = version
    return current


# --------------------------------------------------------------------------- tracks & co


def upsert_track(
    conn: sqlite3.Connection,
    *,
    spotify_id: str,
    title: str,
    isrc: str | None = None,
    duration_ms: int | None = None,
    explicit: bool = False,
    is_local: bool = False,
    album_id: str | None = None,
    added_at: str | None = None,
    base_title: str | None = None,
    mix_name: str | None = None,
    mix_type: str | None = None,
    remixers: tuple[str, ...] | list[str] | None = None,
    is_remix: bool = False,
    title_confidence: float | None = None,
    raw: Mapping[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        insert into tracks (spotify_id, isrc, title, base_title, mix_name, mix_type, remixers_json,
                            is_remix, title_confidence, album_id, duration_ms, explicit, is_local,
                            added_at, raw_json, updated_at)
        values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        on conflict(spotify_id) do update set
            isrc=excluded.isrc, title=excluded.title, base_title=excluded.base_title,
            mix_name=excluded.mix_name, mix_type=excluded.mix_type,
            remixers_json=excluded.remixers_json,
            is_remix=excluded.is_remix, title_confidence=excluded.title_confidence,
            album_id=excluded.album_id, duration_ms=excluded.duration_ms,
            explicit=excluded.explicit,
            is_local=excluded.is_local, added_at=coalesce(excluded.added_at, tracks.added_at),
            raw_json=coalesce(excluded.raw_json, tracks.raw_json), updated_at=excluded.updated_at
        """,
        (
            spotify_id,
            isrc,
            title,
            base_title,
            mix_name,
            mix_type,
            json.dumps(list(remixers)) if remixers else None,
            int(is_remix),
            title_confidence,
            album_id,
            duration_ms,
            int(explicit),
            int(is_local),
            added_at,
            json.dumps(raw) if raw is not None else None,
            _iso(_now()),
        ),
    )


def upsert_artist(
    conn: sqlite3.Connection,
    *,
    spotify_id: str,
    name: str,
    genres: list[str] | None = None,
) -> None:
    conn.execute(
        """
        insert into artists (spotify_id, name, genres_json, fetched_at) values (?,?,?,?)
        on conflict(spotify_id) do update set
            name=excluded.name,
            genres_json=coalesce(excluded.genres_json, artists.genres_json),
            fetched_at=excluded.fetched_at
        """,
        (spotify_id, name, json.dumps(genres) if genres is not None else None, _iso(_now())),
    )


def link_track_artist(
    conn: sqlite3.Connection, *, track_id: str, artist_id: str, position: int = 0
) -> None:
    conn.execute(
        "insert or replace into track_artists (track_id, artist_id, position) values (?,?,?)",
        (track_id, artist_id, position),
    )


def upsert_album(
    conn: sqlite3.Connection,
    *,
    spotify_id: str,
    name: str | None,
    label: str | None = None,
    release_date: str | None = None,
) -> None:
    conn.execute(
        """
        insert into albums (spotify_id, name, label, release_date, fetched_at) values (?,?,?,?,?)
        on conflict(spotify_id) do update set
            name=coalesce(excluded.name, albums.name),
            label=coalesce(excluded.label, albums.label),
            release_date=coalesce(excluded.release_date, albums.release_date),
            fetched_at=excluded.fetched_at
        """,
        (spotify_id, name, label, release_date, _iso(_now())),
    )


# --------------------------------------------------------------------------- evidence


@dataclass(frozen=True)
class Evidence:
    """One raw tag from one source for one track."""

    track_id: str
    source: str
    field: str
    raw_value: str
    source_tag_id: str | None = None
    match_method: str = "isrc"  # isrc | id | fuzzy | fuzzy_ambiguous
    match_score: float = 1.0
    weight: float = 1.0
    meta: Mapping[str, Any] = field(default_factory=dict)


def add_evidence(conn: sqlite3.Connection, ev: Evidence) -> bool:
    """Insert one evidence row. Returns False when the identical row already exists."""
    cur = conn.execute(
        """
        insert or ignore into evidence (track_id, source, field, raw_value, source_tag_id,
                                        match_method, match_score, weight, meta_json, fetched_at)
        values (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ev.track_id,
            ev.source,
            ev.field,
            ev.raw_value,
            ev.source_tag_id,
            ev.match_method,
            ev.match_score,
            ev.weight,
            json.dumps(dict(ev.meta)) if ev.meta else None,
            _iso(_now()),
        ),
    )
    return cur.rowcount == 1


# --------------------------------------------------------------------------- http cache


@dataclass(frozen=True)
class CacheHit:
    status: int
    body: str | None
    fetched_at: datetime
    expires_at: datetime


def _params_hash(params: Mapping[str, Any] | None) -> str:
    payload = json.dumps(params or {}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def cache_put(
    conn: sqlite3.Connection,
    *,
    source: str,
    endpoint: str,
    params: Mapping[str, Any] | None,
    status: int,
    body: str | None,
    ttl: timedelta,
    now: datetime | None = None,
) -> None:
    now = now or _now()
    conn.execute(
        """
        insert or replace into http_cache
            (source, endpoint, params_hash, status, body, fetched_at, expires_at)
        values (?,?,?,?,?,?,?)
        """,
        (source, endpoint, _params_hash(params), status, body, _iso(now), _iso(now + ttl)),
    )


def cache_get(
    conn: sqlite3.Connection,
    *,
    source: str,
    endpoint: str,
    params: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> CacheHit | None:
    now = now or _now()
    row = conn.execute(
        "select status, body, fetched_at, expires_at from http_cache"
        " where source=? and endpoint=? and params_hash=?",
        (source, endpoint, _params_hash(params)),
    ).fetchone()
    if row is None:
        return None
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at <= now:
        return None
    return CacheHit(
        status=int(row["status"]),
        body=row["body"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        expires_at=expires_at,
    )


# --------------------------------------------------------------------------- playlists


@dataclass(frozen=True)
class PlaylistRow:
    axis: str
    category: str
    spotify_id: str
    name: str
    snapshot_id: str | None
    synced_at: str | None


def get_playlist(conn: sqlite3.Connection, *, axis: str, category: str) -> PlaylistRow | None:
    row = conn.execute(
        "select axis, category, spotify_id, name, snapshot_id, synced_at from playlists"
        " where axis=? and category=?",
        (axis, category),
    ).fetchone()
    return PlaylistRow(**dict(row)) if row else None


def set_playlist(
    conn: sqlite3.Connection,
    *,
    axis: str,
    category: str,
    spotify_id: str,
    name: str,
    snapshot_id: str | None = None,
) -> None:
    conn.execute(
        """
        insert into playlists (axis, category, spotify_id, name, snapshot_id, synced_at)
        values (?,?,?,?,?,?)
        on conflict(axis, category) do update set
            spotify_id=excluded.spotify_id, name=excluded.name,
            snapshot_id=excluded.snapshot_id, synced_at=excluded.synced_at
        """,
        (axis, category, spotify_id, name, snapshot_id, _iso(_now())),
    )


def all_playlists(conn: sqlite3.Connection, *, axis: str | None = None) -> list[PlaylistRow]:
    sql = "select axis, category, spotify_id, name, snapshot_id, synced_at from playlists"
    args: tuple[Any, ...] = ()
    if axis:
        sql += " where axis=?"
        args = (axis,)
    return [PlaylistRow(**dict(r)) for r in conn.execute(sql + " order by axis, category", args)]


# --------------------------------------------------------------------------- taxonomy snapshots


def save_taxonomy_snapshot(
    conn: sqlite3.Connection, *, source: str, version: str, payload: Any
) -> None:
    conn.execute(
        "insert or replace into taxonomy_snapshots (source, version, fetched_at, json)"
        " values (?,?,?,?)",
        (source, version, _iso(_now()), json.dumps(payload)),
    )


# --------------------------------------------------------------------------- labels


def add_label(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    track_id: str,
    axis: str,
    category: str,
    weight: float = 1.0,
    kind: str = "seed",
    note: str | None = None,
) -> None:
    conn.execute(
        "insert into labels (session_id, track_id, axis, category, weight, kind, note, created_at)"
        " values (?,?,?,?,?,?,?,?)",
        (session_id, track_id, axis, category, weight, kind, note, _iso(_now())),
    )


def latest_labels(
    conn: sqlite3.Connection, *, axis: str
) -> dict[str, list[tuple[str, float, str]]]:
    """Per track, the labels from that track's most recent session on this axis."""
    rows = conn.execute(
        """
        select l.track_id, l.category, l.weight, l.kind
        from labels l
        join (select track_id, max(id) as max_id from labels where axis=? group by track_id) m
          on m.track_id = l.track_id
        join labels latest on latest.id = m.max_id
        where l.axis = ? and l.session_id = latest.session_id
        order by l.track_id, l.id
        """,
        (axis, axis),
    ).fetchall()
    out: dict[str, list[tuple[str, float, str]]] = {}
    for r in rows:
        out.setdefault(r["track_id"], []).append((r["category"], float(r["weight"]), r["kind"]))
    return out
