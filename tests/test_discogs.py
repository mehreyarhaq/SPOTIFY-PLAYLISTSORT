"""Discogs: search -> release tracklist -> release-level styles for the matched track (mocked)."""

from __future__ import annotations

import json

import httpx
import pytest

from playlistsort import db
from playlistsort.enrich.base import Http
from playlistsort.enrich.discogs import DiscogsEnricher

SEARCH = {
    "results": [
        {
            "id": 500,
            "type": "release",
            "title": "CamelPhat - Cola (Remixes)",
            "year": "2017",
            "label": ["Defected"],
            "genre": ["Electronic"],
            "style": ["Tech House", "Deep House"],
        },
        {
            "id": 501,
            "type": "release",
            "title": "CamelPhat - Cola",
            "year": "2017",
            "label": ["Defected"],
            "genre": ["Electronic"],
            "style": ["Tech House"],
        },
    ]
}
RELEASE_500 = {
    "id": 500,
    "title": "Cola (Remixes)",
    "artists": [{"name": "CamelPhat"}, {"name": "Elderbrook"}],
    "genres": ["Electronic"],
    "styles": ["Tech House", "Deep House"],
    "labels": [{"name": "Defected"}],
    "tracklist": [
        {
            "position": "1",
            "title": "Cola (Franky Rizardo Remix)",
            "duration": "6:52",
            "extraartists": [{"name": "Franky Rizardo", "role": "Remix"}],
        },
        {
            "position": "2",
            "title": "Cola (Solardo Remix)",
            "duration": "6:10",
            "extraartists": [{"name": "Solardo", "role": "Remix"}],
        },
    ],
}
RELEASE_501 = {
    "id": 501,
    "title": "Cola",
    "artists": [{"name": "CamelPhat"}],
    "genres": ["Electronic"],
    "styles": ["Tech House"],
    "labels": [{"name": "Defected"}],
    "tracklist": [{"position": "1", "title": "Cola", "duration": "6:20"}],
}


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
            base_title="Cola",
            mix_name="Franky Rizardo Remix",
            is_remix=True,
            remixers=("Franky Rizardo",),
            duration_ms=412000,
        )
        db.upsert_artist(c, spotify_id="a1", name="CamelPhat")
        db.link_track_artist(c, track_id="t1", artist_id="a1")
    return c


def _api(conn, handler):
    http = Http(
        conn,
        source="discogs",
        base_url="https://api.discogs.test",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    return DiscogsEnricher(http, token="TOK")


def test_matches_the_remix_row_and_marks_release_level_styles(conn) -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Discogs token=TOK"
        assert "playlistsort" in request.headers["user-agent"]
        if request.url.path == "/database/search":
            assert request.url.params["track"] == "Cola"
            return httpx.Response(200, json=SEARCH)
        if request.url.path == "/releases/500":
            return httpx.Response(200, json=RELEASE_500)
        if request.url.path == "/releases/501":
            return httpx.Response(200, json=RELEASE_501)
        return httpx.Response(404)

    stats = _api(conn, handler).enrich(conn)
    assert stats.matched == 1
    rows = conn.execute(
        "select field, raw_value, match_method, meta_json from evidence order by field, raw_value"
    ).fetchall()
    styles = {r["raw_value"] for r in rows if r["field"] == "style"}
    assert styles == {"Tech House", "Deep House"}
    assert {r["raw_value"] for r in rows if r["field"] == "genre"} == {"Electronic"}
    meta = json.loads(rows[0]["meta_json"])
    assert meta["release_id"] == 500
    assert meta["release_level"] is True
    assert meta["track_position"] == "1"
    assert meta["remix_by_other"] is True
    assert all(r["match_method"] in ("fuzzy", "fuzzy_ambiguous") for r in rows)


def test_no_tracklist_match_means_not_found(conn) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/database/search":
            return httpx.Response(200, json={"results": [SEARCH["results"][1]]})
        return httpx.Response(200, json=RELEASE_501)  # only the original mix exists

    stats = _api(conn, handler).enrich(conn)
    assert stats.matched == 0 and stats.not_found == 1
    assert conn.execute("select count(*) from evidence").fetchone()[0] == 0


def test_empty_search_is_not_found_without_release_fetches(conn) -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"results": []})

    _api(conn, handler).enrich(conn)
    assert paths == ["/database/search"]
