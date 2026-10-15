"""Cached, rate-spaced HTTP client used by every enricher (no real network)."""

from __future__ import annotations

import json

import httpx
import pytest

from playlistsort import db
from playlistsort.enrich.base import AuthExpiredError, Http, HttpError


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    return c


def _client(conn, handler, **kw):
    transport = httpx.MockTransport(handler)
    return Http(
        conn,
        source="test",
        base_url="https://api.example.com/v1",
        transport=transport,
        sleep=lambda s: None,
        **kw,
    )


def test_get_json_hits_transport_once_then_serves_from_cache(conn) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True, "q": request.url.params.get("q")})

    http = _client(conn, handler)
    first = http.get_json("/search", params={"q": "cola"})
    second = http.get_json("/search", params={"q": "cola"})
    assert first.status == 200 and first.json == {"ok": True, "q": "cola"}
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.json == first.json
    assert calls == ["https://api.example.com/v1/search?q=cola"]


def test_not_found_is_cached_as_negative_result(conn) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, json={"detail": "not found"})

    http = _client(conn, handler)
    r1 = http.get_json("/tracks/1")
    r2 = http.get_json("/tracks/1")
    assert r1.status == 404 and r1.json is None
    assert r2.from_cache is True
    assert len(calls) == 1


def test_server_errors_are_retried_then_raised(conn) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="down")

    http = _client(conn, handler, max_attempts=3)
    with pytest.raises(HttpError) as exc:
        http.get_json("/x")
    assert exc.value.status == 503
    assert len(calls) == 3
    # nothing cached for a server error
    assert conn.execute("select count(*) from http_cache").fetchone()[0] == 0


def test_rate_limit_waits_for_retry_after_then_succeeds(conn) -> None:
    calls = []
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="slow down")
        return httpx.Response(200, json={"ok": 1})

    transport = httpx.MockTransport(handler)
    http = Http(conn, source="t", base_url="https://x", transport=transport, sleep=sleeps.append)
    r = http.get_json("/y")
    assert r.status == 200
    assert 2 in sleeps


def test_unauthorized_raises_auth_expired(conn) -> None:
    http = _client(conn, lambda req: httpx.Response(401, json={"detail": "nope"}))
    with pytest.raises(AuthExpiredError):
        http.get_json("/me")


def test_min_interval_spaces_requests(conn) -> None:
    sleeps = []
    clock = {"t": 100.0}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"n": request.url.path})

    http = Http(
        conn,
        source="t",
        base_url="https://x",
        transport=httpx.MockTransport(handler),
        sleep=lambda s: (sleeps.append(s), clock.__setitem__("t", clock["t"] + s)),
        clock=lambda: clock["t"],
        min_interval=1.0,
    )
    http.get_json("/a")
    http.get_json("/b")
    assert sleeps and sleeps[0] == pytest.approx(1.0)


def test_post_json_is_cached_by_body(conn) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"results": [1]})

    http = _client(conn, handler)
    http.post_json("/resolve", body={"items": [1, 2]})
    http.post_json("/resolve", body={"items": [1, 2]})
    http.post_json("/resolve", body={"items": [3]})
    assert len(calls) == 2


def test_headers_are_sent(conn) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    http = _client(conn, handler, headers={"x-api-key": "k", "User-Agent": "playlistsort/0.1"})
    http.get_json("/")
    assert seen["x-api-key"] == "k"
    assert seen["user-agent"] == "playlistsort/0.1"
