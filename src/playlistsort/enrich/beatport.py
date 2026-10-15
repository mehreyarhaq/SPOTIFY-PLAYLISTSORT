"""Beatport v4 catalog: per-track genre, sub-genre, BPM, key and label.

Access: either a bearer token pasted from the browser (`BEATPORT_BEARER_TOKEN`) or a login with
the user's own Beatport account through account.beatport.com using the public client id of
Beatport's API-docs app (the route `beets-beatport4` uses). Tokens are short-lived; the refresh
token is kept in `data/tokens/beatport.json`.

Lookup order per track: ISRC -> (Beatport id if another source supplied one) -> search with
asymmetric fuzzy matching, so a remix never matches its original.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from playlistsort import db
from playlistsort.enrich.base import (
    DEFAULT_USER_AGENT,
    AuthExpiredError,
    EnrichStats,
    Evidence,
    Http,
    TrackRow,
    load_tracks,
    query_for,
)
from playlistsort.enrich.match import Candidate, pick_best
from playlistsort.taxonomy.normalize import Taxonomy, normalize

API_BASE = "https://api.beatport.com/v4"
ACCOUNT_BASE = "https://account.beatport.com"
DOCS_CLIENT_ID = "0GIvkCltVIuPkkwSJHp6NDb3s0potTjLBQr388Dd"
REDIRECT_URI = "https://account.beatport.com/o/post-message/?origin=https://api.beatport.com"
MIN_INTERVAL = 0.7  # no published limit; community clients space 0.5-2 s


class BeatportAuthError(RuntimeError):
    pass


class BeatportAuth:
    def __init__(
        self,
        *,
        bearer_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        token_path: Path | None = None,
        client_id: str = DOCS_CLIENT_ID,
        account_transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bearer_token = bearer_token or None
        self.username = username or None
        self.password = password or None
        self.token_path = token_path
        self.client_id = client_id
        self.account_transport = account_transport
        self.clock = clock

    # ------------------------------------------------------------------ public

    def access_token(self, *, force_refresh: bool = False) -> str:
        if self.bearer_token:
            if force_refresh:
                raise BeatportAuthError(
                    "Beatport rejected the pasted token (expired?). Paste a fresh "
                    "BEATPORT_BEARER_TOKEN, or set BEATPORT_USERNAME and BEATPORT_PASSWORD instead."
                )
            return self.bearer_token
        tok = self._load()
        if tok and not force_refresh and float(tok.get("expires_at", 0)) - 60 > self.clock():
            return tok["access_token"]
        if tok and tok.get("refresh_token"):
            try:
                return self._save(
                    self._exchange(
                        {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}
                    )
                )["access_token"]
            except (httpx.HTTPError, BeatportAuthError):
                pass
        if self.username and self.password:
            return self._save(self._login())["access_token"]
        raise BeatportAuthError(
            "No Beatport credentials. Set BEATPORT_BEARER_TOKEN, or BEATPORT_USERNAME and "
            "BEATPORT_PASSWORD, in .env (see README)."
        )

    # ------------------------------------------------------------------ internals

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=ACCOUNT_BASE,
            transport=self.account_transport,
            follow_redirects=False,
            timeout=30,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )

    def _exchange(self, params: dict[str, str]) -> dict[str, Any]:
        with self._client() as client:
            resp = client.post("/o/token/", data={"client_id": self.client_id, **params})
        if resp.status_code != 200:
            raise BeatportAuthError(
                f"Beatport token request failed ({resp.status_code}): {resp.text[:200]}"
            )
        data = resp.json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_at": self.clock() + float(data.get("expires_in", 600)),
        }

    def _login(self) -> dict[str, Any]:
        with self._client() as client:
            resp = client.post(
                "/identity/v1/login/", json={"username": self.username, "password": self.password}
            )
            if resp.status_code >= 400:
                raise BeatportAuthError(
                    f"Beatport login failed ({resp.status_code}); "
                    "check BEATPORT_USERNAME / BEATPORT_PASSWORD."
                )
            auth = client.get(
                "/o/authorize/",
                params={
                    "response_type": "code",
                    "client_id": self.client_id,
                    "redirect_uri": REDIRECT_URI,
                },
            )
        location = auth.headers.get("location")
        if not location:
            raise BeatportAuthError(
                f"Beatport authorize returned no redirect (status {auth.status_code})."
            )
        code = parse_qs(urlparse(location).query).get("code")
        if not code:
            raise BeatportAuthError(f"Beatport authorize redirect carried no code: {location}")
        return self._exchange(
            {"grant_type": "authorization_code", "code": code[0], "redirect_uri": REDIRECT_URI}
        )

    def _load(self) -> dict[str, Any] | None:
        if self.token_path and self.token_path.exists():
            try:
                return json.loads(self.token_path.read_text(encoding="utf-8"))
            except ValueError:
                return None
        return None

    def _save(self, tok: dict[str, Any]) -> dict[str, Any]:
        if self.token_path:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(json.dumps(tok), encoding="utf-8")
        return tok


# --------------------------------------------------------------------------- helpers


def _names(items: Any) -> tuple[str, ...]:
    return tuple(i.get("name") for i in (items or []) if isinstance(i, dict) and i.get("name"))


def _fmt_bpm(bpm: Any) -> str | None:
    try:
        value = float(bpm)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return str(int(value)) if value.is_integer() else str(value)


def candidate_from(bp: dict[str, Any]) -> Candidate:
    return Candidate(
        id=str(bp.get("id")),
        title=bp.get("name") or "",
        mix_name=bp.get("mix_name") or None,
        artists=_names(bp.get("artists")),
        remixers=_names(bp.get("remixers")),
        duration_ms=bp.get("length_ms"),
        isrc=bp.get("isrc"),
    )


def evidence_from_track(
    track: TrackRow, bp: dict[str, Any], *, match_method: str, match_score: float
) -> list[Evidence]:
    key = bp.get("key") or {}
    camelot = None
    if key.get("camelot_number") and key.get("camelot_letter"):
        camelot = f"{key['camelot_number']}{key['camelot_letter']}"
    release = bp.get("release") or {}
    meta = {
        "beatport_id": bp.get("id"),
        "title": bp.get("name"),
        "mix_name": bp.get("mix_name"),
        "isrc": bp.get("isrc"),
        "release": release.get("name"),
        "release_id": release.get("id"),
        "publish_date": bp.get("publish_date"),
        "camelot": camelot,
        "match_score": match_score,
    }

    def ev(field: str, value: str, tag_id: Any = None) -> Evidence:
        return Evidence(
            track_id=track.spotify_id,
            source="beatport",
            field=field,
            raw_value=value,
            source_tag_id=str(tag_id) if tag_id is not None else None,
            match_method=match_method,
            match_score=match_score,
            meta=meta,
        )

    out: list[Evidence] = []
    genre = bp.get("genre") or {}
    if genre.get("name"):
        out.append(ev("genre", genre["name"], genre.get("id")))
    sub = bp.get("sub_genre") or {}
    if sub.get("name"):
        out.append(ev("subgenre", sub["name"], sub.get("id")))
    label = (release.get("label") or {}).get("name")
    if label:
        out.append(ev("label", label, (release.get("label") or {}).get("id")))
    bpm = _fmt_bpm(bp.get("bpm"))
    if bpm:
        out.append(ev("bpm", bpm))
    if key.get("name"):
        out.append(ev("key", key["name"], key.get("id")))
    if bp.get("mix_name"):
        out.append(ev("mix_name", bp["mix_name"]))
    if bp.get("id") is not None:
        out.append(ev("beatport_id", str(bp["id"])))
    return out


# --------------------------------------------------------------------------- enricher


class BeatportEnricher:
    name = "beatport"

    def __init__(self, http: Http, auth: BeatportAuth) -> None:
        self.http = http
        self.auth = auth
        self._set_token(self.auth.access_token())

    def _set_token(self, token: str) -> None:
        self.http.set_header("Authorization", f"Bearer {token}")

    def _get(self, path: str, params: dict[str, Any] | None = None):
        try:
            return self.http.get_json(path, params)
        except AuthExpiredError:
            self._set_token(self.auth.access_token(force_refresh=True))
            return self.http.get_json(path, params)

    def lookup_isrc(self, isrc: str) -> list[dict[str, Any]]:
        resp = self._get("/catalog/tracks/", {"isrc": isrc, "per_page": 10})
        return list((resp.json or {}).get("results") or []) if resp.ok else []

    def lookup_id(self, beatport_id: str | int) -> dict[str, Any] | None:
        resp = self._get(f"/catalog/tracks/{beatport_id}/")
        return resp.json if resp.ok and isinstance(resp.json, dict) else None

    def search(self, query: str) -> list[dict[str, Any]]:
        resp = self._get("/catalog/search/", {"q": query, "type": "tracks", "per_page": 25})
        return list((resp.json or {}).get("tracks") or []) if resp.ok else []

    def find(self, track: TrackRow) -> tuple[dict[str, Any] | None, str, float]:
        """Return (beatport track, match_method, match_score)."""
        if track.isrc:
            hits = self.lookup_isrc(track.isrc)
            if len(hits) == 1:
                return hits[0], "isrc", 1.0
            if hits:
                best = pick_best(query_for(track), [candidate_from(h) for h in hits], min_score=0.0)
                if best:
                    chosen = next(h for h in hits if str(h.get("id")) == best.candidate.id)
                    return chosen, "isrc", best.score
        artist = track.artists[0] if track.artists else ""
        results = self.search(" ".join(p for p in (artist, track.base_title) if p))
        if not results:
            return None, "none", 0.0
        best = pick_best(query_for(track), [candidate_from(r) for r in results])
        if not best:
            return None, "none", 0.0
        chosen = next(r for r in results if str(r.get("id")) == best.candidate.id)
        return chosen, best.method, best.score

    def enrich(
        self,
        conn: sqlite3.Connection,
        *,
        only_missing: bool = True,
        limit: int | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> EnrichStats:
        tracks = load_tracks(conn, missing_source=self.name if only_missing else None, limit=limit)
        stats = EnrichStats(self.name)
        for i, track in enumerate(tracks, 1):
            stats.looked_up += 1
            bp, method, score = self.find(track)
            if bp is None:
                stats.not_found += 1
            else:
                stats.matched += 1
                stats.ambiguous += int(method == "fuzzy_ambiguous")
                with conn:
                    for ev in evidence_from_track(
                        track, bp, match_method=method, match_score=score
                    ):
                        stats.evidence_rows += int(db.add_evidence(conn, ev))
            if progress:
                progress(i, len(tracks))
        return stats


# --------------------------------------------------------------------------- taxonomy


def pull_taxonomy(
    enricher: BeatportEnricher, conn: sqlite3.Connection, *, version: str | None = None
) -> list[dict]:
    """Fetch every genre with its sub-genres and store a snapshot."""
    results: list[dict] = []
    params: dict[str, Any] = {"per_page": 200}
    while True:
        resp = enricher._get("/catalog/genres/", params)
        data = resp.json or {}
        results.extend(data.get("results") or [])
        nxt = data.get("next")
        if not nxt:
            break
        params = {k: v[0] for k, v in parse_qs(urlparse(nxt).query).items()}
    version = version or datetime.now(UTC).strftime("%Y-%m-%d")
    with conn:
        db.save_taxonomy_snapshot(conn, source="beatport", version=version, payload=results)
    return results


def taxonomy_diff(genres: list[dict], taxonomy: Taxonomy) -> dict[str, list[str]]:
    """Names Beatport has that config/taxonomy.yaml does not know yet."""
    missing_genres: list[str] = []
    missing_subgenres: list[str] = []
    for g in genres:
        name = g.get("name") or ""
        if not normalize(name, "beatport", taxonomy, source_tag_id=g.get("id")):
            missing_genres.append(name)
            continue
        for s in g.get("sub_genres") or []:
            sub = s.get("name") or ""
            hit = normalize(f"{name} | {sub}", "beatport", taxonomy, source_tag_id=s.get("id"))
            if not hit or hit[0].specificity != "subgenre":
                missing_subgenres.append(f"{name} | {sub}")
    return {"missing_genres": missing_genres, "missing_subgenres": missing_subgenres}
