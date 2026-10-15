"""Decide whether a source's candidate is the same recording as our track.

The matcher is asymmetric on purpose: a remix query must never match the original and vice
versa, and a remix must be by the same remixer. Titles are compared fuzzily; artists by
overlap; duration as a soft check. Two near-equal candidates mark the match as ambiguous.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from playlistsort.titles import parse_title

MIN_SCORE = 0.72
AMBIGUITY_GAP = 0.05
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


@dataclass(frozen=True)
class Query:
    base_title: str
    mix_name: str | None
    remixers: tuple[str, ...]
    artists: tuple[str, ...]
    duration_ms: int | None
    is_remix: bool


@dataclass(frozen=True)
class Candidate:
    id: str
    title: str
    mix_name: str | None
    artists: tuple[str, ...]
    remixers: tuple[str, ...]
    duration_ms: int | None
    isrc: str | None = None
    extra: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchScore:
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Match:
    candidate: Candidate
    score: float
    method: str  # "fuzzy" | "fuzzy_ambiguous"


def _names_overlap(a: Sequence[str], b: Sequence[str]) -> bool:
    return any(fuzz.partial_ratio(_norm(x), _norm(y)) >= 85 for x in a for y in b if x and y)


def _artist_similarity(query: Sequence[str], candidate: Sequence[str]) -> float:
    if not query or not candidate:
        return 0.5
    hits = sum(
        1 for q in query if any(fuzz.partial_ratio(_norm(q), _norm(c)) >= 85 for c in candidate)
    )
    return hits / len(query)


def _duration_similarity(a: int | None, b: int | None) -> float:
    if not a or not b:
        return 0.6
    diff = abs(a - b)
    if diff <= 3_000:
        return 1.0
    if diff <= 15_000:
        return 0.7
    if diff <= 60_000:
        return 0.3
    return 0.0


def score_candidate(query: Query, cand: Candidate) -> MatchScore:
    full = f"{cand.title} ({cand.mix_name})" if cand.mix_name else cand.title
    parsed = parse_title(full, list(cand.artists) + list(cand.remixers))
    cand_remixers = tuple(parsed.remixers) or tuple(cand.remixers)
    cand_is_remix = parsed.is_remix or bool(cand.remixers)
    reasons: list[str] = []

    title = fuzz.token_set_ratio(_norm(query.base_title), _norm(parsed.base_title)) / 100
    artists = _artist_similarity(query.artists, tuple(cand.artists) + cand_remixers)
    duration = _duration_similarity(query.duration_ms, cand.duration_ms)

    if query.is_remix != cand_is_remix:
        reasons.append("remix/original mismatch")
        return MatchScore(round(0.2 * title, 4), tuple(reasons))
    if query.is_remix:
        if query.remixers and cand_remixers:
            if _names_overlap(query.remixers, cand_remixers):
                mix = 1.0
            else:
                reasons.append("different remixer")
                return MatchScore(round(0.3 * title, 4), tuple(reasons))
        else:
            mix = 0.6
            reasons.append("remixer unnamed on one side")
    else:
        mix = 1.0

    score = 0.5 * title + 0.25 * artists + 0.15 * mix + 0.10 * duration
    if duration < 0.5:
        reasons.append("duration differs")
    if artists < 0.5:
        reasons.append("artists differ")
    return MatchScore(round(score, 4), tuple(reasons))


def pick_best(
    query: Query,
    candidates: Sequence[Candidate],
    *,
    min_score: float = MIN_SCORE,
    ambiguity_gap: float = AMBIGUITY_GAP,
) -> Match | None:
    if not candidates:
        return None
    scored = sorted(((score_candidate(query, c).score, c) for c in candidates), key=lambda t: -t[0])
    best_score, best = scored[0]
    if best_score < min_score:
        return None
    ambiguous = (
        len(scored) > 1
        and (best_score - scored[1][0]) < ambiguity_gap
        and best.id != scored[1][1].id
    )
    return Match(
        candidate=best, score=best_score, method="fuzzy_ambiguous" if ambiguous else "fuzzy"
    )
