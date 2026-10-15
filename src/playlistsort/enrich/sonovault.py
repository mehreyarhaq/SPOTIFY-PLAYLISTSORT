"""SonoVault (sonovault.now): bulk-resolve Spotify ids to genre, sub-genre and label.

Free tier: 1000 requests/month, 20/min; `POST /tracks/resolve` takes up to 100 items.
Credits per item vs per request is unverified; the enricher reports request counts so the
user can check their dashboard after the first batch.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from playlistsort import db
from playlistsort.enrich.base import EnrichStats, Evidence, Http, TrackRow, load_tracks

BASE_URL = "https://api.sonovault.now/v1"  # verify against sonovault.now/docs on first live run
BATCH = 100


def evidence_from_track(track: TrackRow, tr: dict[str, Any]) -> list[Evidence]:
    releases = tr.get("releases") or []
    label = next(
        (r["label"]["name"] for r in releases if r.get("label") and r["label"].get("name")), None
    )
    remixers = [a["name"] for a in tr.get("artists") or [] if a.get("is_remixer") and a.get("name")]
    meta = {
        "sonovault_id": tr.get("id"),
        "title": tr.get("title"),
        "isrc": tr.get("isrc"),
        "isrc_match": bool(track.isrc and tr.get("isrc") and track.isrc == tr.get("isrc")),
        "remixers": remixers,
        "release": releases[0].get("title") if releases else None,
    }

    def ev(field: str, value: str) -> Evidence:
        return Evidence(
            track_id=track.spotify_id,
            source="sonovault",
            field=field,
            raw_value=value,
            match_method="id",
            match_score=1.0,
            meta=meta,
        )

    out = [ev("genre", g) for g in tr.get("genre") or [] if g]
    out += [ev("subgenre", s) for s in tr.get("subgenre") or [] if s]
    if label:
        out.append(ev("label", label))
    return out


class SonoVaultEnricher:
    name = "sonovault"

    def __init__(self, http: Http) -> None:
        self.http = http

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
        for start in range(0, len(tracks), BATCH):
            batch = tracks[start : start + BATCH]
            body = {
                "input_type": "spotify_id",
                "items": [{"spotify_id": t.spotify_id} for t in batch],
            }
            resp = self.http.post_json("/tracks/resolve", body)
            if not resp.from_cache:
                stats.requests += 1
            results = (resp.json or {}).get("results") or []
            if len(results) < len(batch):
                stats.errors += len(batch) - len(results)
            with conn:
                for track, row in zip(batch, results, strict=False):
                    stats.looked_up += 1
                    tr = row.get("track") if isinstance(row, dict) else None
                    if not tr:
                        stats.not_found += 1
                        continue
                    stats.matched += 1
                    for ev in evidence_from_track(track, tr):
                        stats.evidence_rows += int(db.add_evidence(conn, ev))
            if progress:
                progress(min(start + BATCH, len(tracks)), len(tracks))
        return stats
