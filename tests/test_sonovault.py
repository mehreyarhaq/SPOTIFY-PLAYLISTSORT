"""SonoVault bulk resolve -> evidence (mocked transport)."""

from __future__ import annotations

import json

import httpx
import pytest

from playlistsort import db
from playlistsort.enrich.base import Http
from playlistsort.enrich.sonovault import SonoVaultEnricher

TRACK_OK = {
    "status": "matched",
    "track": {
        "id": 101,
        "title": "Cola (Franky Rizardo Remix)",
        "isrc": "GB1",
        "duration": 412,
        "genre": ["House"],
        "subgenre": ["Tech House"],
        "artists": [
            {"id": 1, "name": "CamelPhat", "is_primary": True},
            {"id": 2, "name": "Franky Rizardo", "is_remixer": True},
        ],
        "releases": [
            {
                "id": 9,
                "title": "Cola (Remixes)",
                "artist": {"id": 1, "name": "CamelPhat"},
                "label": {"id": 5, "name": "Defected"},
                "release_date": "2017-11-10",
            }
        ],
    },
}
TRACK_MISS = {"status": "not_found", "track": None}


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
            is_remix=True,
            remixers=("Franky Rizardo",),
            duration_ms=412000,
        )
        db.upsert_track(
            c, spotify_id="t2", title="Rej", isrc="GB2", base_title="Rej", duration_ms=300000
        )
    return c


def _enricher(conn, handler):
    http = Http(
        conn,
        source="sonovault",
        base_url="https://api.sonovault.test/v1",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    return SonoVaultEnricher(http)


def test_resolve_batches_by_spotify_id_and_writes_evidence(conn) -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/tracks/resolve")
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"results": [TRACK_OK, TRACK_MISS]})

    stats = _enricher(conn, handler).enrich(conn)
    assert len(bodies) == 1
    assert bodies[0]["input_type"] == "spotify_id"
    assert [i["spotify_id"] for i in bodies[0]["items"]] == ["t1", "t2"]
    assert stats.looked_up == 2 and stats.matched == 1 and stats.not_found == 1

    rows = conn.execute(
        "select field, raw_value, match_method, meta_json from evidence"
        " where track_id='t1' order by field, raw_value"
    ).fetchall()
    got = {(r["field"], r["raw_value"]) for r in rows}
    assert got == {("genre", "House"), ("subgenre", "Tech House"), ("label", "Defected")}
    assert all(r["match_method"] == "id" for r in rows)
    meta = json.loads(rows[0]["meta_json"])
    assert meta["sonovault_id"] == 101
    assert meta["isrc_match"] is True


def test_second_run_uses_cache_and_adds_no_duplicates(conn) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"results": [TRACK_OK, TRACK_MISS]})

    e = _enricher(conn, handler)
    e.enrich(conn)
    before = conn.execute("select count(*) from evidence").fetchone()[0]
    e.enrich(conn, only_missing=False)
    after = conn.execute("select count(*) from evidence").fetchone()[0]
    assert len(calls) == 1
    assert before == after == 3


def test_only_missing_skips_tracks_that_already_have_sonovault_evidence(conn) -> None:
    with conn:
        db.add_evidence(
            conn, db.Evidence(track_id="t1", source="sonovault", field="genre", raw_value="House")
        )
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"results": [TRACK_MISS]})

    _enricher(conn, handler).enrich(conn)
    assert [i["spotify_id"] for i in bodies[0]["items"]] == ["t2"]


def test_batches_are_capped_at_100(conn) -> None:
    with conn:
        for i in range(150):
            db.upsert_track(conn, spotify_id=f"x{i}", title=f"T{i}", base_title=f"T{i}")
    sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        items = json.loads(request.content)["items"]
        sizes.append(len(items))
        return httpx.Response(200, json={"results": [TRACK_MISS] * len(items)})

    _enricher(conn, handler).enrich(conn)
    assert sizes == [100, 52]
