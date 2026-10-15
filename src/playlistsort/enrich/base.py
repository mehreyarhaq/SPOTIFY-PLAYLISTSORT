"""Shared plumbing for metadata sources.

`Http` wraps httpx with: a persistent cache in the `http_cache` table (positive results for a
year, 404s for 30 days, so re-runs never re-fetch), minimum spacing between requests, retries
with backoff on 429/5xx (honouring `Retry-After`), and typed errors so enrichers can react
(for example Beatport refreshing its token on `AuthExpiredError`).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx

from playlistsort import __version__, db
from playlistsort.db import Evidence

__all__ = ["AuthExpiredError", "Evidence", "Http", "HttpError", "Response"]

DEFAULT_USER_AGENT = (
    f"playlistsort/{__version__} (personal DJ library tool; "
    "+https://github.com/mehreyarhaq/SPOTIFY-PLAYLISTSORT)"
)


class HttpError(RuntimeError):
    def __init__(self, status: int, message: str, url: str = "") -> None:
        super().__init__(f"HTTP {status} for {url}: {message[:300]}")
        self.status = status
        self.message = message
        self.url = url


class AuthExpiredError(HttpError):
    """401/403: the credential is missing, expired or not allowed."""


@dataclass(frozen=True)
class Response:
    status: int
    json: Any | None
    from_cache: bool

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Http:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        source: str,
        base_url: str,
        headers: Mapping[str, str] | None = None,
        min_interval: float = 0.0,
        timeout: float = 30.0,
        max_attempts: int = 4,
        positive_ttl: timedelta = timedelta(days=365),
        negative_ttl: timedelta = timedelta(days=30),
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], Any] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.conn = conn
        self.source = source
        self.min_interval = min_interval
        self.max_attempts = max(1, max_attempts)
        self.positive_ttl = positive_ttl
        self.negative_ttl = negative_ttl
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "application/json",
                **(headers or {}),
            },
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------ public API

    def set_header(self, name: str, value: str | None) -> None:
        if value is None:
            self._client.headers.pop(name, None)
        else:
            self._client.headers[name] = value

    def get_json(
        self, path: str, params: Mapping[str, Any] | None = None, *, cache: bool = True
    ) -> Response:
        return self._request("GET", path, params=params, body=None, cache=cache)

    def post_json(self, path: str, body: Any, *, cache: bool = True) -> Response:
        return self._request("POST", path, params=None, body=body, cache=cache)

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ internals

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None,
        body: Any,
        cache: bool,
    ) -> Response:
        key = {"method": method, "params": dict(params or {}), "body": body}
        if cache:
            hit = db.cache_get(self.conn, source=self.source, endpoint=path, params=key)
            if hit is not None:
                data = _parse(hit.body) if 200 <= hit.status < 300 else None
                return Response(hit.status, data, True)

        resp = self._send(method, path, params=params, body=body)
        status = resp.status_code
        url = str(resp.request.url)
        if 200 <= status < 300:
            if cache:
                self._store(path, key, status, resp.text, self.positive_ttl)
            return Response(status, _parse(resp.text), False)
        if status in (404, 410):
            if cache:
                self._store(path, key, 404, None, self.negative_ttl)
            return Response(404, None, False)
        if status in (401, 403):
            raise AuthExpiredError(status, resp.text, url)
        raise HttpError(status, resp.text, url)

    def _store(
        self, path: str, key: dict[str, Any], status: int, body: str | None, ttl: timedelta
    ) -> None:
        with self.conn:
            db.cache_put(
                self.conn,
                source=self.source,
                endpoint=path,
                params=key,
                status=status,
                body=body,
                ttl=ttl,
            )

    def _pace(self) -> None:
        if self.min_interval and self._last_request is not None:
            elapsed = self._clock() - self._last_request
            if elapsed < self.min_interval:
                self._sleep(self.min_interval - elapsed)
        self._last_request = self._clock()

    def _send(
        self, method: str, path: str, *, params: Mapping[str, Any] | None, body: Any
    ) -> httpx.Response:
        last_exc: Exception | None = None
        resp: httpx.Response | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._pace()
            try:
                resp = self._client.request(method, path, params=params, json=body)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < self.max_attempts:
                    self._sleep(min(30.0, 0.5 * 2**attempt))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self.max_attempts:
                    return resp
                self._sleep(_retry_after(resp, default=min(30.0, 0.5 * 2**attempt)))
                continue
            return resp
        if resp is not None:
            return resp
        raise HttpError(0, f"transport failure: {last_exc}", path)


def _retry_after(resp: httpx.Response, *, default: float) -> float:
    raw = resp.headers.get("Retry-After")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return default


def _parse(text: str | None) -> Any | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return {"_raw": text}


# --------------------------------------------------------------------------- tracks to enrich


@dataclass(frozen=True)
class TrackRow:
    spotify_id: str
    title: str
    base_title: str
    mix_name: str | None
    mix_type: str | None
    remixers: tuple[str, ...]
    is_remix: bool
    isrc: str | None
    duration_ms: int | None
    artists: tuple[str, ...]
    album_id: str | None
    album_name: str | None
    label: str | None


def load_tracks(
    conn: sqlite3.Connection,
    *,
    missing_source: str | None = None,
    include_local: bool = False,
    limit: int | None = None,
) -> list[TrackRow]:
    """Tracks in library order (oldest first), optionally only those lacking a source's evidence."""
    sql = """
        select t.spotify_id, t.title, t.base_title, t.mix_name, t.mix_type, t.remixers_json,
               t.is_remix, t.isrc, t.duration_ms, t.album_id, a.name as album_name, a.label,
               (select group_concat(ar.name, char(31))
                  from track_artists ta join artists ar on ar.spotify_id = ta.artist_id
                 where ta.track_id = t.spotify_id order by ta.position) as artist_names
        from tracks t left join albums a on a.spotify_id = t.album_id
        where 1=1
    """
    args: list[Any] = []
    if not include_local:
        sql += " and t.is_local = 0"
    if missing_source:
        sql += (
            " and not exists (select 1 from evidence e"
            "  where e.track_id = t.spotify_id and e.source = ?)"
        )
        args.append(missing_source)
    sql += " order by t.added_at, t.spotify_id"
    if limit is not None:
        sql += " limit ?"
        args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    out: list[TrackRow] = []
    for r in rows:
        remixers = tuple(json.loads(r["remixers_json"])) if r["remixers_json"] else ()
        artists = tuple(r["artist_names"].split(chr(31))) if r["artist_names"] else ()
        out.append(
            TrackRow(
                spotify_id=r["spotify_id"],
                title=r["title"],
                base_title=r["base_title"] or r["title"],
                mix_name=r["mix_name"],
                mix_type=r["mix_type"],
                remixers=remixers,
                is_remix=bool(r["is_remix"]),
                isrc=r["isrc"],
                duration_ms=r["duration_ms"],
                artists=artists,
                album_id=r["album_id"],
                album_name=r["album_name"],
                label=r["label"],
            )
        )
    return out


def query_for(track: TrackRow):
    from playlistsort.enrich.match import Query

    return Query(
        base_title=track.base_title,
        mix_name=track.mix_name,
        remixers=track.remixers,
        artists=track.artists,
        duration_ms=track.duration_ms,
        is_remix=track.is_remix,
    )


@dataclass
class EnrichStats:
    source: str
    looked_up: int = 0
    matched: int = 0
    ambiguous: int = 0
    not_found: int = 0
    evidence_rows: int = 0
    errors: int = 0
    requests: int = 0

    def summary(self) -> str:
        return (
            f"{self.source}: {self.matched}/{self.looked_up} matched"
            f" ({self.ambiguous} ambiguous, {self.not_found} not found, {self.errors} errors),"
            f" {self.evidence_rows} evidence rows"
        )
