"""Parsing of Spotify saved-track items (no network)."""

from __future__ import annotations

from playlistsort.spotify.ingest import TrackRecord, parse_saved_item

ITEM = {
    "added_at": "2026-09-01T12:00:00Z",
    "track": {
        "id": "4iV5W9uYEdYUVa79Axb7Rh",
        "uri": "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
        "name": "Cola - Franky Rizardo Remix",
        "duration_ms": 412000,
        "explicit": False,
        "is_local": False,
        "external_ids": {"isrc": "GBKPL1700123"},
        "album": {"id": "alb1", "name": "Cola (Remixes)", "release_date": "2017-11-10"},
        "artists": [
            {"id": "a1", "name": "CamelPhat"},
            {"id": "a2", "name": "Elderbrook"},
            {"id": "a3", "name": "Franky Rizardo"},
        ],
    },
}

LOCAL_ITEM = {
    "added_at": "2026-09-02T12:00:00Z",
    "track": {
        "id": None,
        "uri": "spotify:local:Someone:Album:White+Label:300",
        "name": "White Label",
        "duration_ms": 300000,
        "explicit": False,
        "is_local": True,
        "external_ids": {},
        "album": {"id": None, "name": "Album", "release_date": None},
        "artists": [{"id": None, "name": "Someone"}],
    },
}


def test_parses_regular_item() -> None:
    rec = parse_saved_item(ITEM)
    assert isinstance(rec, TrackRecord)
    assert rec.spotify_id == "4iV5W9uYEdYUVa79Axb7Rh"
    assert rec.uri == "spotify:track:4iV5W9uYEdYUVa79Axb7Rh"
    assert rec.isrc == "GBKPL1700123"
    assert rec.duration_ms == 412000
    assert rec.album_id == "alb1"
    assert rec.album_name == "Cola (Remixes)"
    assert rec.album_release_date == "2017-11-10"
    assert rec.artists == (("a1", "CamelPhat"), ("a2", "Elderbrook"), ("a3", "Franky Rizardo"))
    assert rec.added_at == "2026-09-01T12:00:00Z"
    assert rec.is_local is False
    # title parsing is applied on the way in
    assert rec.parsed.base_title == "Cola"
    assert rec.parsed.remixers == ("Franky Rizardo",)


def test_local_item_uses_uri_as_id_and_has_no_album() -> None:
    rec = parse_saved_item(LOCAL_ITEM)
    assert rec.is_local is True
    assert rec.spotify_id == "spotify:local:Someone:Album:White+Label:300"
    assert rec.isrc is None
    assert rec.album_id is None
    assert rec.artists == ((None, "Someone"),)


def test_missing_track_returns_none() -> None:
    assert parse_saved_item({"added_at": "x", "track": None}) is None
