"""Schema, migrations and the small repository helpers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from playlistsort import db

EXPECTED_TABLES = {
    "tracks",
    "artists",
    "track_artists",
    "albums",
    "evidence",
    "taxonomy_snapshots",
    "http_cache",
    "labels",
    "assignments",
    "playlists",
    "schema_version",
}


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def _tables(c: sqlite3.Connection) -> set[str]:
    rows = c.execute("select name from sqlite_master where type='table'").fetchall()
    return {r[0] for r in rows}


def test_fresh_migration_creates_core_tables(conn: sqlite3.Connection) -> None:
    assert _tables(conn) >= EXPECTED_TABLES


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    before = db.schema_version(conn)
    assert before >= 1
    db.migrate(conn)
    assert db.schema_version(conn) == before


def test_duplicate_evidence_is_rejected_by_schema(conn: sqlite3.Connection) -> None:
    db.upsert_track(conn, spotify_id="t1", title="Cola", isrc="GBX", duration_ms=1000)
    sql = (
        "insert into evidence(track_id, source, field, raw_value,"
        " match_method, match_score, weight)"
        " values ('t1','beatport','genre','Tech House','isrc',1.0,1.0)"
    )
    conn.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_add_evidence_helper_is_idempotent(conn: sqlite3.Connection) -> None:
    db.upsert_track(conn, spotify_id="t1", title="Cola", isrc="GBX", duration_ms=1000)
    ev = db.Evidence(track_id="t1", source="beatport", field="genre", raw_value="Tech House")
    assert db.add_evidence(conn, ev) is True
    assert db.add_evidence(conn, ev) is False
    assert conn.execute("select count(*) from evidence").fetchone()[0] == 1


def test_upsert_track_updates_in_place(conn: sqlite3.Connection) -> None:
    db.upsert_track(conn, spotify_id="t1", title="Cola", isrc="GBX", duration_ms=1000)
    db.upsert_track(
        conn, spotify_id="t1", title="Cola - Extended Mix", isrc="GBX", duration_ms=2000
    )
    rows = conn.execute("select title, duration_ms from tracks").fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "Cola - Extended Mix"
    assert rows[0]["duration_ms"] == 2000


def test_http_cache_roundtrip_and_expiry(conn: sqlite3.Connection) -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    db.cache_put(
        conn,
        source="deezer",
        endpoint="track/isrc",
        params={"isrc": "GBX"},
        status=200,
        body='{"ok": true}',
        now=now,
        ttl=timedelta(days=30),
    )
    hit = db.cache_get(
        conn, source="deezer", endpoint="track/isrc", params={"isrc": "GBX"}, now=now
    )
    assert hit is not None
    assert hit.status == 200
    assert hit.body == '{"ok": true}'
    later = now + timedelta(days=31)
    assert (
        db.cache_get(
            conn, source="deezer", endpoint="track/isrc", params={"isrc": "GBX"}, now=later
        )
        is None
    )


def test_http_cache_key_ignores_param_order(conn: sqlite3.Connection) -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    db.cache_put(
        conn,
        source="s",
        endpoint="e",
        params={"a": 1, "b": 2},
        status=404,
        body="",
        now=now,
        ttl=timedelta(days=1),
    )
    assert (
        db.cache_get(conn, source="s", endpoint="e", params={"b": 2, "a": 1}, now=now) is not None
    )


def test_playlist_mapping_roundtrip(conn: sqlite3.Connection) -> None:
    assert db.get_playlist(conn, axis="genre", category="tech_house") is None
    db.set_playlist(
        conn, axis="genre", category="tech_house", spotify_id="pl1", name="[PS] Tech House"
    )
    row = db.get_playlist(conn, axis="genre", category="tech_house")
    assert row is not None
    assert row.spotify_id == "pl1"
    assert row.name == "[PS] Tech House"
