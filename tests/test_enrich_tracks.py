"""Selecting tracks for enrichment and turning rows into match queries."""

from __future__ import annotations

import pytest

from playlistsort import db
from playlistsort.enrich.base import load_tracks, query_for


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    with c:
        db.upsert_track(
            c,
            spotify_id="t1",
            title="Cola - Franky Rizardo Remix",
            isrc="GB1",
            duration_ms=412000,
            base_title="Cola",
            mix_name="Franky Rizardo Remix",
            mix_type="remix",
            remixers=("Franky Rizardo",),
            is_remix=True,
            album_id="al1",
        )
        db.upsert_track(
            c, spotify_id="t2", title="Rej", isrc="GB2", duration_ms=300000, base_title="Rej"
        )
        db.upsert_track(c, spotify_id="spotify:local:x", title="White Label", is_local=True)
        for aid, name, pos in [("a1", "CamelPhat", 0), ("a2", "Elderbrook", 1)]:
            db.upsert_artist(c, spotify_id=aid, name=name)
            db.link_track_artist(c, track_id="t1", artist_id=aid, position=pos)
        db.upsert_artist(c, spotify_id="a3", name="Âme")
        db.link_track_artist(c, track_id="t2", artist_id="a3")
        db.upsert_album(c, spotify_id="al1", name="Cola (Remixes)", label="Defected")
        db.add_evidence(
            c, db.Evidence(track_id="t2", source="beatport", field="genre", raw_value="Deep House")
        )
    return c


def test_load_tracks_returns_rows_with_artists_and_label(conn) -> None:
    rows = {r.spotify_id: r for r in load_tracks(conn)}
    t1 = rows["t1"]
    assert t1.artists == ("CamelPhat", "Elderbrook")
    assert t1.label == "Defected"
    assert t1.remixers == ("Franky Rizardo",)
    assert t1.is_remix is True
    assert t1.isrc == "GB1"


def test_load_tracks_skips_local_files_by_default(conn) -> None:
    ids = {r.spotify_id for r in load_tracks(conn)}
    assert "spotify:local:x" not in ids


def test_load_tracks_missing_source_filters_already_enriched(conn) -> None:
    ids = [r.spotify_id for r in load_tracks(conn, missing_source="beatport")]
    assert ids == ["t1"]
    assert [r.spotify_id for r in load_tracks(conn, missing_source="discogs")] == ["t1", "t2"]


def test_load_tracks_limit(conn) -> None:
    assert len(load_tracks(conn, limit=1)) == 1


def test_query_for_builds_match_query(conn) -> None:
    t1 = next(r for r in load_tracks(conn) if r.spotify_id == "t1")
    q = query_for(t1)
    assert q.base_title == "Cola"
    assert q.remixers == ("Franky Rizardo",)
    assert q.artists == ("CamelPhat", "Elderbrook")
    assert q.duration_ms == 412000
    assert q.is_remix is True
