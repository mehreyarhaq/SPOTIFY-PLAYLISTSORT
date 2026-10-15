"""From a track's genre probabilities to the playlists it belongs in."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from playlistsort.model.scorer import TrackScore, Weights


@dataclass(frozen=True)
class Assignment:
    category: str  # canonical tag: "tech_house" or "minimal_deep_tech.deep_tech"
    prob: float
    kind: str  # primary | secondary | subgenre


def assignments_for(
    score: TrackScore,
    *,
    weights: Weights,
    threshold: float | None = None,
    sub_share_min: float = 0.5,
    sub_coverage_min: float = 0.3,
) -> list[Assignment]:
    """Primary genre above the threshold; its sub-genre when the evidence names one clearly;
    the runner-up too when the track sits between two genres."""
    threshold = weights.threshold if threshold is None else threshold
    if score.status == "unknown" or score.top is None:
        return []
    top_tag, top_prob = score.top
    if top_prob < threshold:
        return []
    out = [Assignment(top_tag, round(top_prob, 4), "primary")]
    sub = score.top_subgenre(top_tag)
    if sub and sub[1] >= sub_share_min and score.sub_coverage(top_tag) >= sub_coverage_min:
        out.append(Assignment(sub[0], round(top_prob * sub[1], 4), "subgenre"))
    if score.status == "between" and score.second is not None:
        second_tag, second_prob = score.second
        out.append(Assignment(second_tag, round(second_prob, 4), "secondary"))
    return out


def write_assignments(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    axis: str,
    per_track: Mapping[str, Sequence[Assignment]],
) -> int:
    """Replace the assignments of `run_id` for `axis`. Returns the number of rows written."""
    n = 0
    with conn:
        conn.execute("delete from assignments where run_id=? and axis=?", (run_id, axis))
        for track_id, assigns in per_track.items():
            for a in assigns:
                conn.execute(
                    "insert or replace into assignments (run_id, track_id, axis, category, prob)"
                    " values (?,?,?,?,?)",
                    (run_id, track_id, axis, a.category, a.prob),
                )
                n += 1
    return n
