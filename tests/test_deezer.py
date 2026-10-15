"""Deezer ISRC lookup -> preview availability and ids (mocked transport)."""

from __future__ import annotations

import httpx
import pytest

from playlistsort import db
from playlistsort.enrich.base import Http
from playlistsort.enrich.deezer import DeezerEnricher

HIT = {
    "id": 555,
    "title": "Cola (Franky Rizardo Remix)",
    "isrc": "GB1",
    "duration": 412,
    "bpm": 124.0,
    "gain": -8.1,
    "preview": "https://cdnt-preview.dzcdn.net/api/1/1/x.mp3?hdnea=exp=1",
    "album": {"id": 77, "title": "Cola (Remixes)"},
    "artist": {"id": 3, "name": "CamelPhat"},
}
MISS = {"error": {"type": "DataException", "message": "no data", "code": 800}}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    with c:
        db.upsert_track(
            c, spotify_id="t1", title="Cola", isrc="GB1", base_title="Cola", duration_ms=412000
        )
        db.upsert_track(c, spotify_id="t2", title="Rej", isrc="GB2", base_title="Rej")
        db.upsert_track(c, spotify_id="t3", title="No ISRC", base_title="No ISRC")
    return c


def _enricher(conn, handler):
    http = Http(
        conn,
        source="deezer",
        base_url="https://api.deezer.test",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    return DeezerEnricher(http)


def test_isrc_lookup_records_ids_preview_and_bpm_without_storing_the_signed_url(conn) -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/track/isrc:GB1":
            return httpx.Response(200, json=HIT)
        return httpx.Response(200, json=MISS)

    stats = _enricher(conn, handler).enrich(conn)
    assert paths == ["/track/isrc:GB1", "/track/isrc:GB2"]  # t3 has no ISRC -> skipped
    assert stats.looked_up == 2 and stats.matched == 1 and stats.not_found == 1
    rows = conn.execute(
        "select field, raw_value, meta_json from evidence where track_id='t1'"
    ).fetchall()
    fields = {r["field"]: r["raw_value"] for r in rows}
    assert fields["deezer_id"] == "555"
    assert fields["bpm"] == "124.0"
    assert fields["preview_available"] == "true"
    for r in rows:
        assert "dzcdn" not in (r["meta_json"] or "")
        assert "dzcdn" not in r["raw_value"]


def test_miss_leaves_no_evidence(conn) -> None:
    _enricher(conn, lambda req: httpx.Response(200, json=MISS)).enrich(conn)
    assert conn.execute("select count(*) from evidence").fetchone()[0] == 0


def test_preview_url_is_fetched_live_not_from_evidence(conn) -> None:
    e = _enricher(conn, lambda req: httpx.Response(200, json=HIT))
    url = e.preview_url("GB1")
    assert url and url.startswith("https://cdnt-preview.dzcdn.net/")
