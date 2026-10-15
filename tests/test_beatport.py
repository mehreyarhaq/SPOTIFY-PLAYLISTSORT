"""Beatport v4: token handling, ISRC lookup, search fallback, taxonomy pull (mocked)."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from playlistsort import db
from playlistsort.enrich.base import Http
from playlistsort.enrich.beatport import (
    BeatportAuth,
    BeatportAuthError,
    BeatportEnricher,
    pull_taxonomy,
    taxonomy_diff,
)
from playlistsort.taxonomy.normalize import load_taxonomy

BP_TRACK = {
    "id": 123,
    "name": "Cola",
    "mix_name": "Franky Rizardo Remix",
    "isrc": "GB1",
    "length_ms": 412000,
    "bpm": 124,
    "key": {"id": 5, "name": "A Minor", "camelot_number": 8, "camelot_letter": "A"},
    "genre": {"id": 11, "name": "Tech House"},
    "sub_genre": {"id": 300, "name": "Latin Tech"},
    "artists": [{"id": 1, "name": "CamelPhat"}, {"id": 2, "name": "Elderbrook"}],
    "remixers": [{"id": 3, "name": "Franky Rizardo"}],
    "release": {"id": 9, "name": "Cola (Remixes)", "label": {"id": 5, "name": "Defected"}},
    "publish_date": "2017-11-10",
}
BP_ORIGINAL = {
    **BP_TRACK,
    "id": 124,
    "mix_name": "Original Mix",
    "remixers": [],
    "isrc": "GB0",
    "sub_genre": None,
    "length_ms": 380000,
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


def _api(conn, handler, auth=None):
    http = Http(
        conn,
        source="beatport",
        base_url="https://api.beatport.test/v4",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    return BeatportEnricher(http, auth or BeatportAuth(bearer_token="pasted-token"))


# ------------------------------------------------------------------ auth


def test_pasted_bearer_token_is_used_as_is() -> None:
    auth = BeatportAuth(bearer_token="abc")
    assert auth.access_token() == "abc"


def test_login_flow_obtains_and_persists_tokens(tmp_path) -> None:
    seen = []

    def account(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/identity/v1/login/":
            body = json.loads(request.content)
            assert body == {"username": "dj", "password": "pw"}
            return httpx.Response(
                200, json={"ok": True}, headers={"set-cookie": "sessionid=S1; Path=/"}
            )
        if request.url.path == "/o/authorize/":
            assert "sessionid=S1" in request.headers.get("cookie", "")
            assert request.url.params["client_id"]
            return httpx.Response(
                302, headers={"location": "https://account.beatport.test/o/post-message/?code=C0DE"}
            )
        if request.url.path == "/o/token/":
            form = dict(p.split("=", 1) for p in request.content.decode().split("&"))
            assert form["grant_type"] == "authorization_code" and form["code"] == "C0DE"
            return httpx.Response(
                200, json={"access_token": "AT1", "refresh_token": "RT1", "expires_in": 600}
            )
        return httpx.Response(404)

    auth = BeatportAuth(
        username="dj",
        password="pw",
        token_path=tmp_path / "bp.json",
        account_transport=httpx.MockTransport(account),
        clock=lambda: 1000.0,
    )
    assert auth.access_token() == "AT1"
    assert [p for _, p in seen] == ["/identity/v1/login/", "/o/authorize/", "/o/token/"]
    saved = json.loads((tmp_path / "bp.json").read_text())
    assert saved["refresh_token"] == "RT1"
    # second call within lifetime: no new requests
    assert auth.access_token() == "AT1"
    assert len(seen) == 3


def test_expired_token_is_refreshed_with_refresh_token(tmp_path) -> None:
    (tmp_path / "bp.json").write_text(
        json.dumps({"access_token": "OLD", "refresh_token": "RT1", "expires_at": 900})
    )
    forms = []

    def account(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/o/token/"
        forms.append(dict(p.split("=", 1) for p in request.content.decode().split("&")))
        return httpx.Response(
            200, json={"access_token": "NEW", "refresh_token": "RT2", "expires_in": 600}
        )

    auth = BeatportAuth(
        token_path=tmp_path / "bp.json",
        account_transport=httpx.MockTransport(account),
        clock=lambda: 1000.0,
    )
    assert auth.access_token() == "NEW"
    assert forms[0]["grant_type"] == "refresh_token" and forms[0]["refresh_token"] == "RT1"


def test_no_credentials_raises_clear_error(tmp_path) -> None:
    auth = BeatportAuth(
        token_path=tmp_path / "none.json",
        account_transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    with pytest.raises(BeatportAuthError):
        auth.access_token()


# ------------------------------------------------------------------ enrichment


def test_isrc_hit_writes_genre_subgenre_label_bpm_key(conn) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer pasted-token"
        assert request.url.path == "/v4/catalog/tracks/" and request.url.params["isrc"] == "GB1"
        return httpx.Response(200, json={"results": [BP_TRACK], "next": None})

    stats = _api(conn, handler).enrich(conn)
    assert stats.matched == 1
    rows = {
        (r["field"], r["raw_value"]): r
        for r in conn.execute("select * from evidence where track_id='t1'")
    }
    assert ("genre", "Tech House") in rows and rows[("genre", "Tech House")][
        "source_tag_id"
    ] == "11"
    assert ("subgenre", "Latin Tech") in rows and rows[("subgenre", "Latin Tech")][
        "source_tag_id"
    ] == "300"
    assert ("label", "Defected") in rows
    assert ("bpm", "124") in rows
    assert ("key", "A Minor") in rows
    assert all(r["match_method"] == "isrc" for r in rows.values())
    meta = json.loads(rows[("genre", "Tech House")]["meta_json"])
    assert meta["beatport_id"] == 123 and meta["camelot"] == "8A"


def test_isrc_miss_falls_back_to_search_and_picks_the_right_mix(conn) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v4/catalog/tracks/":
            return httpx.Response(200, json={"results": [], "next": None})
        if request.url.path == "/v4/catalog/search/":
            assert "Cola" in request.url.params["q"]
            return httpx.Response(200, json={"tracks": [BP_ORIGINAL, BP_TRACK]})
        return httpx.Response(404)

    stats = _api(conn, handler).enrich(conn)
    assert stats.matched == 1
    row = conn.execute(
        "select match_method, meta_json from evidence where field='genre'"
    ).fetchone()
    assert row["match_method"] == "fuzzy"
    assert json.loads(row["meta_json"])["beatport_id"] == 123  # the remix, not the original


def test_unauthorized_triggers_one_token_refresh_and_retry(conn) -> None:
    tokens = iter(["T1", "T2"])

    class FakeAuth(BeatportAuth):
        def __init__(self):
            super().__init__(bearer_token=None)
            self.refreshes = 0

        def access_token(self, *, force_refresh=False):
            if force_refresh:
                self.refreshes += 1
            return next(tokens)

    auth = FakeAuth()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        if request.headers["authorization"] == "Bearer T1":
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json={"results": [BP_TRACK], "next": None})

    stats = _api(conn, handler, auth=auth).enrich(conn)
    assert stats.matched == 1
    assert seen == ["Bearer T1", "Bearer T2"]
    assert auth.refreshes == 1


# ------------------------------------------------------------------ taxonomy


def test_pull_taxonomy_paginates_and_stores_snapshot(conn) -> None:
    pages = {
        "/v4/catalog/genres/": {
            "results": [
                {"id": 11, "name": "Tech House", "sub_genres": [{"id": 300, "name": "Latin Tech"}]}
            ],
            "next": "https://api.beatport.test/v4/catalog/genres/?page=2&per_page=200",
        },
        "page2": {
            "results": [{"id": 999, "name": "Brand New Genre", "sub_genres": []}],
            "next": None,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        key = "page2" if request.url.params.get("page") == "2" else request.url.path
        return httpx.Response(200, json=pages[key])

    genres = pull_taxonomy(_api(conn, handler), conn, version="test")
    assert [g["name"] for g in genres] == ["Tech House", "Brand New Genre"]
    snap = conn.execute("select source, version from taxonomy_snapshots").fetchone()
    assert (snap["source"], snap["version"]) == ("beatport", "test")

    tax = load_taxonomy("config/taxonomy.yaml")
    diff = taxonomy_diff(genres, tax)
    assert diff["missing_genres"] == ["Brand New Genre"]
    assert diff["missing_subgenres"] == []


def test_time_is_importable_for_default_clock() -> None:
    assert callable(time.time)
