"""Review CSV out, user labels in.

Export: one row per track with the current scores and a compact evidence summary, plus empty
`your_genre` / `also` / `note` columns for the user. Import: reads those columns back.
  your_genre = "Tech House"            -> seed label
  your_genre = "Deep House", also = "Tech House" -> two labels of weight 0.5, kind "between"
  your_genre = "not Afro House"        -> negative label
Categories may be written as tag keys, display names, "Genre › Sub-genre" paths or any alias.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from playlistsort import db
from playlistsort.model.scorer import GENRE_FIELDS, TrackScore
from playlistsort.taxonomy.normalize import Taxonomy, normalize, slug

REVIEW_COLUMNS = [
    "track_id",
    "artists",
    "title",
    "label",
    "status",
    "top1",
    "top1_prob",
    "top2",
    "top2_prob",
    "top_subgenre",
    "evidence",
    "spotify_url",
    "your_genre",
    "also",
    "note",
]
_PATH_SEP = re.compile(r"\s*(?:›|>|\||/{2,})\s*")
_STATUS_ORDER = {"between": 0, "unknown": 1, "ok": 2}


def resolve_category(text: str, taxonomy: Taxonomy) -> str | None:
    """Map what a human typed to a canonical tag, or None."""
    t = (text or "").strip()
    if not t:
        return None
    if t in taxonomy.tags():
        return t
    parts = _PATH_SEP.split(t)
    if len(parts) == 2:
        hit = normalize(f"{parts[0]} | {parts[1]}", "beatport", taxonomy)
        if hit and hit[0].specificity == "subgenre":
            return hit[0].tag
    by_path = {slug(taxonomy.path_name(tag)): tag for tag in taxonomy.tags()}
    if slug(t) in by_path:
        return by_path[slug(t)]
    hit = normalize(t, "user", taxonomy)
    if not hit:
        hit = normalize(t, "beatport", taxonomy)
    hit = tuple(c for c in hit if c.specificity != "family")
    if len(hit) == 1:
        return hit[0].tag
    return None


def _evidence_summary(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "select track_id, source, field, raw_value from evidence"
        " order by track_id, source, field, raw_value"
    ).fetchall()
    per_track: dict[str, dict[str, list[str]]] = {}
    for r in rows:
        if r["field"] not in GENRE_FIELDS:
            continue
        per_track.setdefault(r["track_id"], {}).setdefault(r["source"], []).append(r["raw_value"])
    return {
        tid: " | ".join(f"{src}: {', '.join(vals)}" for src, vals in sources.items())
        for tid, sources in per_track.items()
    }


def export_review_csv(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    scores: Mapping[str, TrackScore],
    taxonomy: Taxonomy,
    only: str = "all",
) -> int:
    tracks = conn.execute(
        """
        select t.spotify_id, t.title, a.label,
               (select group_concat(ar.name, ', ') from track_artists ta
                  join artists ar on ar.spotify_id = ta.artist_id
                 where ta.track_id = t.spotify_id order by ta.position) as artists
        from tracks t left join albums a on a.spotify_id = t.album_id
        where t.is_local = 0
        """
    ).fetchall()
    summaries = _evidence_summary(conn)

    def name(tag: str | None) -> str:
        return taxonomy.path_name(tag) if tag else ""

    out_rows = []
    for t in tracks:
        s = scores.get(t["spotify_id"])
        status = s.status if s else "unknown"
        if only == "review" and status == "ok":
            continue
        top = s.top if s else None
        second = s.second if s else None
        sub = s.top_subgenre(top[0]) if (s and top) else None
        out_rows.append(
            {
                "track_id": t["spotify_id"],
                "artists": t["artists"] or "",
                "title": t["title"],
                "label": t["label"] or "",
                "status": status,
                "top1": name(top[0]) if top else "",
                "top1_prob": f"{top[1]:.2f}" if top else "",
                "top2": name(second[0]) if second else "",
                "top2_prob": f"{second[1]:.2f}" if second else "",
                "top_subgenre": name(sub[0]) if sub else "",
                "evidence": summaries.get(t["spotify_id"], ""),
                "spotify_url": f"https://open.spotify.com/track/{t['spotify_id']}",
                "your_genre": "",
                "also": "",
                "note": "",
                "_margin": s.margin if s else 0.0,
            }
        )
    out_rows.sort(
        key=lambda r: (_STATUS_ORDER.get(r["status"], 9), r["_margin"], r["artists"], r["title"])
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)
    return len(out_rows)


@dataclass
class ImportResult:
    session_id: str
    labels: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def import_labels_csv(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    taxonomy: Taxonomy,
    axis: str = "genre",
    session_id: str | None = None,
) -> ImportResult:
    session_id = session_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    result = ImportResult(session_id=session_id)
    with Path(path).open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    with conn:
        for i, row in enumerate(rows, start=2):
            track_id = (row.get("track_id") or "").strip()
            primary = (row.get("your_genre") or "").strip()
            also = (row.get("also") or "").strip()
            note = (row.get("note") or "").strip() or None
            if not track_id or not primary:
                result.skipped += 1
                continue
            negative = primary.lower().startswith("not ")
            cat = resolve_category(primary[4:] if negative else primary, taxonomy)
            if cat is None:
                result.errors.append(f"line {i}: cannot resolve genre {primary!r} for {track_id}")
                continue
            if negative:
                db.add_label(
                    conn,
                    session_id=session_id,
                    track_id=track_id,
                    axis=axis,
                    category=cat,
                    weight=1.0,
                    kind="negative",
                    note=note,
                )
                result.labels += 1
                continue
            if also:
                other = resolve_category(also, taxonomy)
                if other is None:
                    result.errors.append(f"line {i}: cannot resolve genre {also!r} for {track_id}")
                    continue
                for c in (cat, other):
                    db.add_label(
                        conn,
                        session_id=session_id,
                        track_id=track_id,
                        axis=axis,
                        category=c,
                        weight=0.5,
                        kind="between",
                        note=note,
                    )
                result.labels += 2
                continue
            db.add_label(
                conn,
                session_id=session_id,
                track_id=track_id,
                axis=axis,
                category=cat,
                weight=1.0,
                kind="seed",
                note=note,
            )
            result.labels += 1
    return result
