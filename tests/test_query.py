"""`query`: pick tracks by genre and/or feel from the latest run, ordered for mixing."""

from __future__ import annotations

from pathlib import Path

import pytest

from playlistsort import db
from playlistsort.model.assign import Assignment, write_assignments
from playlistsort.model.query import QueryError, select_tracks
from playlistsort.taxonomy.normalize import load_taxonomy

ROOT = Path(__file__).resolve().parents[1]
TAX = load_taxonomy(ROOT / "config" / "taxonomy.yaml")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    feats = {
        "a": {
            "bpm": 124,
            "rms_db": -14,
            "camelot": "8A",
            "crest_db": 10,
            "sub_bass_ratio": 0.2,
            "hp_ratio": 0.6,
            "tempogram_peak": 3,
            "pump": 0.3,
            "spectral_centroid_hz": 2000,
        },
        "b": {
            "bpm": 128,
            "rms_db": -8,
            "camelot": "9A",
            "crest_db": 8,
            "sub_bass_ratio": 0.3,
            "hp_ratio": 0.3,
            "tempogram_peak": 4,
            "pump": 0.6,
            "spectral_centroid_hz": 3500,
        },
        "c": {
            "bpm": 121,
            "rms_db": -16,
            "camelot": "5A",
            "crest_db": 12,
            "sub_bass_ratio": 0.1,
            "hp_ratio": 0.7,
            "tempogram_peak": 2,
            "pump": 0.2,
            "spectral_centroid_hz": 1500,
        },
    }
    with c:
        for tid, f in feats.items():
            db.upsert_track(c, spotify_id=tid, title=tid, base_title=tid)
            db.upsert_features(c, track_id=tid, input_kind="local", values=f)
        db.upsert_track(c, spotify_id="d", title="d", base_title="d")  # no features
    write_assignments(
        c,
        run_id="r1",
        axis="genre",
        per_track={
            "a": [Assignment("tech_house", 0.9, "primary")],
            "b": [Assignment("tech_house", 0.8, "primary")],
            "c": [Assignment("deep_house", 0.9, "primary")],
            "d": [Assignment("tech_house", 0.7, "primary")],
        },
    )
    return c


def test_genre_only_returns_the_genre_ordered_for_mixing(conn) -> None:
    sel = select_tracks(conn, taxonomy=TAX, genre="Tech House")
    assert sel.track_ids == ["a", "b", "d"]  # 124 -> 128 BPM, unknown last
    assert sel.category == "tech_house"


def test_genre_and_feel_intersect(conn) -> None:
    sel = select_tracks(conn, taxonomy=TAX, genre="tech_house", feel="peak-time driving")
    assert sel.track_ids == ["b"]


def test_feel_only_spans_genres(conn) -> None:
    sel = select_tracks(conn, taxonomy=TAX, feel="deep & slow")
    assert set(sel.track_ids) == {"c"}


def test_unknown_genre_or_feel_raise(conn) -> None:
    with pytest.raises(QueryError):
        select_tracks(conn, taxonomy=TAX, genre="polka")
    with pytest.raises(QueryError):
        select_tracks(conn, taxonomy=TAX, feel="sleepy")
    with pytest.raises(QueryError):
        select_tracks(conn, taxonomy=TAX)
