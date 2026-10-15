"""Discogs: release-level styles for the release that contains our exact track/remix.

Discogs styles live on the release, not the track, so the enricher searches releases, fetches
each candidate release's tracklist, matches our track against the tracklist entries (remix
aware), and records the release styles with `release_level: true` and whether the matched
entry is a remix by someone other than the release artist (the scorer down-weights those).
Rate limit: 60 requests/min with a token.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from rapidfuzz import fuzz

from playlistsort import db
from playlistsort.enrich.base import EnrichStats, Evidence, Http, TrackRow, load_tracks, query_for
from playlistsort.enrich.match import Candidate, pick_best

BASE_URL = "https://api.discogs.com"
MIN_INTERVAL = 1.05
MAX_RELEASES = 5


def _names(items: Any) -> tuple[str, ...]:
    return tuple(i.get("name") for i in (items or []) if isinstance(i, dict) and i.get("name"))


def _duration_ms(text: str | None) -> int | None:
    if not text or ":" not in text:
        return None
    try:
        parts = [int(p) for p in text.split(":")]
    except ValueError:
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds * 1000


def _overlaps(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return any(fuzz.partial_ratio(x.lower(), y.lower()) >= 85 for x in a for y in b)


class DiscogsEnricher:
    name = "discogs"

    def __init__(self, http: Http, *, token: str | None) -> None:
        self.http = http
        if token:
            self.http.set_header("Authorization", f"Discogs token={token}")

    def search(self, track: TrackRow) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"type": "release", "per_page": 8, "track": track.base_title}
        if track.artists:
            params["artist"] = track.artists[0]
        resp = self.http.get_json("/database/search", params)
        return list((resp.json or {}).get("results") or []) if resp.ok else []

    def release(self, release_id: int | str) -> dict[str, Any] | None:
        resp = self.http.get_json(f"/releases/{release_id}")
        return resp.json if resp.ok and isinstance(resp.json, dict) else None

    def find(self, track: TrackRow) -> tuple[dict[str, Any] | None, Candidate | None, str, float]:
        results = self.search(track)
        if not results:
            return None, None, "none", 0.0
        releases: dict[int, dict[str, Any]] = {}
        candidates: list[Candidate] = []
        for res in results[:MAX_RELEASES]:
            rid = res.get("id")
            rel = self.release(rid) if rid is not None else None
            if not rel:
                continue
            releases[int(rid)] = rel
            rel_artists = _names(rel.get("artists"))
            for entry in rel.get("tracklist") or []:
                if entry.get("type_") == "heading" or not entry.get("title"):
                    continue
                remixers = tuple(
                    ea.get("name")
                    for ea in entry.get("extraartists") or []
                    if ea.get("name") and "remix" in (ea.get("role") or "").lower()
                )
                candidates.append(
                    Candidate(
                        id=f"{rid}:{entry.get('position')}",
                        title=entry["title"],
                        mix_name=None,
                        artists=_names(entry.get("artists")) or rel_artists,
                        remixers=remixers,
                        duration_ms=_duration_ms(entry.get("duration")),
                        extra={
                            "release_id": int(rid),
                            "position": str(entry.get("position") or ""),
                        },
                    )
                )
        best = pick_best(query_for(track), candidates)
        if not best:
            return None, None, "none", 0.0
        rel = releases[int(best.candidate.extra["release_id"])]
        return rel, best.candidate, best.method, best.score

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
            rel, cand, method, score = self.find(track)
            if rel is None or cand is None:
                stats.not_found += 1
            else:
                stats.matched += 1
                stats.ambiguous += int(method == "fuzzy_ambiguous")
                with conn:
                    for ev in evidence_from_release(
                        track, rel, cand, match_method=method, match_score=score
                    ):
                        stats.evidence_rows += int(db.add_evidence(conn, ev))
            if progress:
                progress(i, len(tracks))
        return stats


def evidence_from_release(
    track: TrackRow,
    rel: dict[str, Any],
    cand: Candidate,
    *,
    match_method: str,
    match_score: float,
) -> list[Evidence]:
    rel_artists = _names(rel.get("artists"))
    remix_by_other = bool(cand.remixers) and not _overlaps(cand.remixers, rel_artists)
    labels = _names(rel.get("labels"))
    meta = {
        "release_id": rel.get("id"),
        "release_title": rel.get("title"),
        "release_level": True,
        "track_position": str(cand.extra.get("position") or ""),
        "track_title": cand.title,
        "remix_by_other": remix_by_other,
        "year": rel.get("year"),
        "match_score": match_score,
    }

    def ev(field: str, value: str) -> Evidence:
        return Evidence(
            track_id=track.spotify_id,
            source="discogs",
            field=field,
            raw_value=value,
            match_method=match_method,
            match_score=match_score,
            meta=meta,
        )

    out = [ev("style", s) for s in rel.get("styles") or [] if s]
    out += [ev("genre", g) for g in rel.get("genres") or [] if g]
    if labels:
        out.append(ev("label", labels[0]))
    return out
