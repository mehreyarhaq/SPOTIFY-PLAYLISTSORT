"""Turn a track's evidence rows into a probability distribution over genres.

score(track, tag) = sum over evidence of
    source_weight * specificity_factor * match_method_cap * alias_share * remix_factor

Sub-genre votes also credit their parent genre, so genre-level probabilities are comparable
across sources that stop at the genre level. Broad (family) tags add a thin, equal vote to
every genre in the family: they cannot pick a genre but they can break a tie between families.
Canonicalisation happens here, at score time, against the current taxonomy.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from playlistsort.taxonomy.normalize import Taxonomy, normalize

GENRE_FIELDS = frozenset({"genre", "subgenre", "style", "tag"})
DEFAULT_SOURCE_WEIGHT = 0.3


@dataclass(frozen=True)
class EvidenceRow:
    track_id: str
    source: str
    field: str
    raw_value: str
    source_tag_id: str | None
    match_method: str
    match_score: float
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Weights:
    sources: Mapping[str, Mapping[str, float]]
    specificity_factor: Mapping[str, float]
    match_method_cap: Mapping[str, float]
    threshold: float
    review_margin: float
    min_evidence_weight: float

    def source_weight(self, source: str) -> float:
        return float(self.sources.get(source, {}).get("weight", DEFAULT_SOURCE_WEIGHT))

    def remix_factor(self, source: str) -> float:
        return float(self.sources.get(source, {}).get("remix_factor", 1.0))

    def scale_by_score(self, source: str) -> bool:
        return bool(self.sources.get(source, {}).get("scale_by_score", False))

    def cap(self, match_method: str) -> float:
        return float(self.match_method_cap.get(match_method, 0.5))

    def specificity(self, level: str) -> float:
        return float(self.specificity_factor.get(level, 1.0))


def load_weights(path: str | Path) -> Weights:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    assignment = data.get("assignment") or {}
    return Weights(
        sources=data.get("sources") or {},
        specificity_factor=data.get("specificity_factor") or {},
        match_method_cap=data.get("match_method_cap") or {},
        threshold=float(assignment.get("threshold", 0.45)),
        review_margin=float(assignment.get("review_margin", 0.15)),
        min_evidence_weight=float(assignment.get("min_evidence_weight", 0.3)),
    )


@dataclass(frozen=True)
class TrackScore:
    track_id: str
    scores: Mapping[str, float]  # tag -> accumulated weight (genre and sub-genre tags)
    probs: Mapping[str, float]  # genre tag -> probability
    family_votes: Mapping[str, float]
    total_weight: float
    status: str  # ok | between | unknown
    contributions: tuple[tuple[str, str, str, float], ...]  # (source, raw, tag, weight)

    @property
    def ranked(self) -> list[tuple[str, float]]:
        if self.status == "unknown":
            return []
        return sorted(self.probs.items(), key=lambda kv: -kv[1])

    @property
    def top(self) -> tuple[str, float] | None:
        r = self.ranked
        return r[0] if r else None

    @property
    def second(self) -> tuple[str, float] | None:
        r = self.ranked
        return r[1] if len(r) > 1 else None

    @property
    def margin(self) -> float:
        r = self.ranked
        if not r:
            return 0.0
        return r[0][1] - (r[1][1] if len(r) > 1 else 0.0)

    @property
    def family_hint(self) -> str | None:
        if not self.family_votes:
            return None
        return max(self.family_votes.items(), key=lambda kv: kv[1])[0]

    @property
    def entropy(self) -> float:
        return -sum(p * math.log(p) for p in self.probs.values() if p > 0)

    def top_subgenre(self, genre_key: str) -> tuple[str, float] | None:
        """Best sub-genre under `genre_key` and its share of the sub-genre votes for that genre."""
        subs = [(t, w) for t, w in self.scores.items() if t.startswith(genre_key + ".")]
        sub_total = sum(w for _, w in subs)
        if not subs or sub_total <= 0:
            return None
        tag, w = max(subs, key=lambda kv: kv[1])
        return tag, w / sub_total

    def sub_coverage(self, genre_key: str) -> float:
        """Fraction of this genre's weight that came with a sub-genre (0 = nobody said which)."""
        parent = self.scores.get(genre_key, 0.0)
        if parent <= 0:
            return 0.0
        sub_total = sum(w for t, w in self.scores.items() if t.startswith(genre_key + "."))
        return min(1.0, sub_total / parent)


def score_track(
    track_id: str,
    evidence: Sequence[EvidenceRow],
    *,
    taxonomy: Taxonomy,
    weights: Weights,
    is_remix: bool = False,
) -> TrackScore:
    scores: dict[str, float] = defaultdict(float)
    family_votes: dict[str, float] = defaultdict(float)
    contributions: list[tuple[str, str, str, float]] = []
    total = 0.0

    for row in evidence:
        if row.field not in GENRE_FIELDS:
            continue
        canon = normalize(row.raw_value, row.source, taxonomy, source_tag_id=row.source_tag_id)
        if not canon:
            continue
        base = weights.source_weight(row.source) * weights.cap(row.match_method)
        if weights.scale_by_score(row.source):
            base *= max(0.0, min(1.0, row.match_score))
        meta = row.meta or {}
        if is_remix and meta.get("release_level"):
            base *= weights.remix_factor(row.source)
        for c in canon:
            w = base * weights.specificity(c.specificity) * c.weight
            if w <= 0:
                continue
            total += w
            contributions.append((row.source, row.raw_value, c.tag, round(w, 4)))
            if c.specificity == "family":
                family_votes[c.family] += w
                for g in taxonomy.genres.values():
                    if g.family == c.family:
                        scores[g.key] += w
                continue
            assert c.genre_key is not None
            scores[c.genre_key] += w
            family_votes[c.family] += w
            if c.subgenre_key:
                scores[c.tag] += w

    genre_scores = {k: v for k, v in scores.items() if "." not in k}
    genre_total = sum(genre_scores.values())
    probs = {k: v / genre_total for k, v in genre_scores.items()} if genre_total > 0 else {}

    if total < weights.min_evidence_weight or not probs:
        status = "unknown"
    else:
        ranked = sorted(probs.values(), reverse=True)
        margin = ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0)
        status = "between" if margin < weights.review_margin else "ok"

    return TrackScore(
        track_id=track_id,
        scores=dict(scores),
        probs=probs,
        family_votes=dict(family_votes),
        total_weight=round(total, 6),
        status=status,
        contributions=tuple(contributions),
    )


# --------------------------------------------------------------------------- loading from the db


def load_evidence(
    conn: sqlite3.Connection, track_ids: Iterable[str] | None = None
) -> dict[str, list[EvidenceRow]]:
    """Evidence rows per track, plus Spotify artist genres as weak `spotify_artist` rows."""
    out: dict[str, list[EvidenceRow]] = defaultdict(list)
    where = ""
    args: list[Any] = []
    if track_ids is not None:
        ids = list(track_ids)
        if not ids:
            return {}
        where = f" where track_id in ({','.join('?' * len(ids))})"
        args = ids
    for r in conn.execute(
        "select track_id, source, field, raw_value, source_tag_id,"
        " match_method, match_score, meta_json"
        f" from evidence{where}",
        args,
    ):
        out[r["track_id"]].append(
            EvidenceRow(
                track_id=r["track_id"],
                source=r["source"],
                field=r["field"],
                raw_value=r["raw_value"],
                source_tag_id=r["source_tag_id"],
                match_method=r["match_method"],
                match_score=float(r["match_score"]),
                meta=json.loads(r["meta_json"]) if r["meta_json"] else {},
            )
        )
    artist_where = where.replace("track_id", "ta.track_id") if where else ""
    for r in conn.execute(
        "select ta.track_id, ar.genres_json from track_artists ta"
        " join artists ar on ar.spotify_id = ta.artist_id"
        f"{artist_where}",
        args,
    ):
        for g in json.loads(r["genres_json"] or "[]"):
            out[r["track_id"]].append(
                EvidenceRow(
                    track_id=r["track_id"],
                    source="spotify_artist",
                    field="genre",
                    raw_value=g,
                    source_tag_id=None,
                    match_method="id",
                    match_score=1.0,
                )
            )
    return dict(out)


def score_library(
    conn: sqlite3.Connection,
    *,
    taxonomy: Taxonomy,
    weights: Weights,
    track_ids: Iterable[str] | None = None,
) -> dict[str, TrackScore]:
    evidence = load_evidence(conn, track_ids)
    rows = conn.execute("select spotify_id, is_remix from tracks where is_local = 0").fetchall()
    wanted = set(track_ids) if track_ids is not None else None
    out: dict[str, TrackScore] = {}
    for r in rows:
        tid = r["spotify_id"]
        if wanted is not None and tid not in wanted:
            continue
        out[tid] = score_track(
            tid,
            evidence.get(tid, []),
            taxonomy=taxonomy,
            weights=weights,
            is_remix=bool(r["is_remix"]),
        )
    return out
