"""Turning scores into playlist assignments."""

from __future__ import annotations

from pathlib import Path

import pytest

from playlistsort import db
from playlistsort.model.assign import Assignment, assignments_for, write_assignments
from playlistsort.model.scorer import EvidenceRow, load_weights, score_track
from playlistsort.spotify.publish import desired_by_category
from playlistsort.taxonomy.normalize import load_taxonomy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tax():
    return load_taxonomy(ROOT / "config" / "taxonomy.yaml")


@pytest.fixture(scope="module")
def weights():
    return load_weights(ROOT / "config" / "weights.yaml")


def ev(source, field, raw, *, method="isrc", tag_id=None):
    return EvidenceRow("t", source, field, raw, tag_id, method, 1.0)


def _kinds(assigns):
    return {(a.category, a.kind) for a in assigns}


def test_clear_genre_with_subgenre_gets_both_playlists(tax, weights) -> None:
    s = score_track(
        "t",
        [
            ev("beatport", "genre", "Minimal / Deep Tech", tag_id="14"),
            ev("beatport", "subgenre", "Deep Tech", tag_id="188"),
        ],
        taxonomy=tax,
        weights=weights,
    )
    assigns = assignments_for(s, weights=weights)
    assert _kinds(assigns) == {
        ("minimal_deep_tech", "primary"),
        ("minimal_deep_tech.deep_tech", "subgenre"),
    }
    assert all(isinstance(a, Assignment) for a in assigns)


def test_between_tracks_land_in_both_genres(tax, weights) -> None:
    s = score_track(
        "t",
        [
            ev("beatport", "genre", "Tech House", tag_id="11"),
            ev("sonovault", "genre", "Deep House", method="id"),
        ],
        taxonomy=tax,
        weights=weights,
    )
    assigns = assignments_for(s, weights=weights)
    assert _kinds(assigns) == {("tech_house", "primary"), ("deep_house", "secondary")}


def test_unknown_gets_nothing(tax, weights) -> None:
    s = score_track("t", [], taxonomy=tax, weights=weights)
    assert assignments_for(s, weights=weights) == []


def test_below_threshold_gets_nothing(tax, weights) -> None:
    rows = [
        ev("discogs", "style", "Deep House", method="fuzzy"),
        ev("discogs", "style", "Tech House", method="fuzzy"),
        ev("discogs", "style", "Progressive House", method="fuzzy"),
    ]
    s = score_track("t", rows, taxonomy=tax, weights=weights)
    assert assignments_for(s, weights=weights) == []
    assert assignments_for(s, weights=weights, threshold=0.3) != []


def test_subgenre_needs_enough_coverage(tax, weights) -> None:
    rows = [
        ev("beatport", "genre", "Tech House", tag_id="11"),
        ev("lastfm", "tag", "afro tech", method="id"),
    ]
    s = score_track("t", rows, taxonomy=tax, weights=weights)
    assigns = assignments_for(s, weights=weights)
    assert [a.category for a in assigns] == ["tech_house"]


def test_write_assignments_feeds_the_publisher() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn)
    with conn:
        db.upsert_track(conn, spotify_id="a", title="A", added_at="2026-01-01")
        db.upsert_track(conn, spotify_id="b", title="B", added_at="2026-01-02")
    per_track = {
        "a": [Assignment("tech_house", 0.9, "primary")],
        "b": [Assignment("tech_house", 0.6, "primary"), Assignment("deep_house", 0.4, "secondary")],
    }
    n = write_assignments(conn, run_id="r1", axis="genre", per_track=per_track)
    assert n == 3
    desired = desired_by_category(conn, axis="genre", run_id="r1")
    assert desired["tech_house"] == ["spotify:track:a", "spotify:track:b"]
    assert desired["deep_house"] == ["spotify:track:b"]
    # re-running the same run id replaces rather than duplicates
    write_assignments(
        conn, run_id="r1", axis="genre", per_track={"a": [Assignment("deep_house", 1.0, "primary")]}
    )
    assert desired_by_category(conn, axis="genre", run_id="r1") == {
        "deep_house": ["spotify:track:a"]
    }
