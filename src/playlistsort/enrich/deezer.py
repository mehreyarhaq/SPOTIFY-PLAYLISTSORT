"""Deezer public API: confirm ISRCs, learn Deezer ids, BPM and whether a 30 s preview exists.

`GET /track/isrc:{ISRC}` needs no key. Missing tracks come back as HTTP 200 with an `error`
object. Preview URLs are signed and expire, so they are never stored; `preview_url` fetches a
fresh one when the audio step needs it (Milestone 4).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from playlistsort import db
from playlistsort.enrich.base import EnrichStats, Evidence, Http, TrackRow, load_tracks

BASE_URL = "https://api.deezer.com"
MIN_INTERVAL = 0.12  # ~50 requests / 5 s per IP


def _clean(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data or "error" in data or not data.get("id"):
        return None
    return data


def evidence_from_track(track: TrackRow, dz: dict[str, Any]) -> list[Evidence]:
    meta = {
        "title": dz.get("title"),
        "artist": (dz.get("artist") or {}).get("name"),
        "album": (dz.get("album") or {}).get("title"),
        "isrc_match": bool(track.isrc and dz.get("isrc") == track.isrc),
        "gain": dz.get("gain"),
        "duration_s": dz.get("duration"),
    }

    def ev(field: str, value: str) -> Evidence:
        return Evidence(
            track_id=track.spotify_id,
            source="deezer",
            field=field,
            raw_value=value,
            match_method="isrc",
            match_score=1.0,
            meta=meta,
        )

    out = [ev("deezer_id", str(dz["id"]))]
    album_id = (dz.get("album") or {}).get("id")
    if album_id:
        out.append(ev("deezer_album_id", str(album_id)))
    bpm = dz.get("bpm")
    if bpm:
        out.append(ev("bpm", str(float(bpm))))
    out.append(ev("preview_available", "true" if dz.get("preview") else "false"))
    return out


class DeezerEnricher:
    name = "deezer"

    def __init__(self, http: Http) -> None:
        self.http = http

    def lookup(self, isrc: str, *, cache: bool = True) -> dict[str, Any] | None:
        resp = self.http.get_json(f"/track/isrc:{isrc}", cache=cache)
        return _clean(resp.json if isinstance(resp.json, dict) else None)

    def preview_url(self, isrc: str) -> str | None:
        """Live fetch (uncached) because the URL is signed and short-lived."""
        data = self.lookup(isrc, cache=False)
        return (data or {}).get("preview") or None

    def enrich(
        self,
        conn: sqlite3.Connection,
        *,
        only_missing: bool = True,
        limit: int | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> EnrichStats:
        tracks = [
            t
            for t in load_tracks(
                conn, missing_source=self.name if only_missing else None, limit=limit
            )
            if t.isrc
        ]
        stats = EnrichStats(self.name)
        for i, track in enumerate(tracks, 1):
            stats.looked_up += 1
            data = self.lookup(track.isrc or "")
            if data is None:
                stats.not_found += 1
            else:
                stats.matched += 1
                with conn:
                    for ev in evidence_from_track(track, data):
                        stats.evidence_rows += int(db.add_evidence(conn, ev))
            if progress:
                progress(i, len(tracks))
        return stats
