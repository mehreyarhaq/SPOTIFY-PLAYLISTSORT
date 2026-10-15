"""Feel axes from signal features, and playlist ordering for DJs."""

from __future__ import annotations

import pytest

from playlistsort import db
from playlistsort.model.feel import (
    FEEL_AXES,
    camelot_distance,
    feel_axes,
    feel_bucket,
    order_for_set,
)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    rows = {
        "slow": {
            "bpm": 118,
            "rms_db": -16,
            "crest_db": 12,
            "sub_bass_ratio": 0.10,
            "hp_ratio": 0.7,
            "tempogram_peak": 2.0,
            "pump": 0.2,
            "spectral_centroid_hz": 1500,
            "camelot": "8A",
        },
        "mid": {
            "bpm": 125,
            "rms_db": -12,
            "crest_db": 10,
            "sub_bass_ratio": 0.20,
            "hp_ratio": 0.5,
            "tempogram_peak": 3.0,
            "pump": 0.4,
            "spectral_centroid_hz": 2500,
            "camelot": "9A",
        },
        "peak": {
            "bpm": 132,
            "rms_db": -8,
            "crest_db": 8,
            "sub_bass_ratio": 0.30,
            "hp_ratio": 0.3,
            "tempogram_peak": 4.0,
            "pump": 0.6,
            "spectral_centroid_hz": 3500,
            "camelot": "8B",
        },
        "noaudio": None,
    }
    with c:
        for tid, f in rows.items():
            db.upsert_track(c, spotify_id=tid, title=tid, base_title=tid)
            if f:
                db.upsert_features(c, track_id=tid, input_kind="local", values=f)
    return c


def test_feel_axes_are_percentiles_in_unit_range(conn) -> None:
    axes = feel_axes(conn)
    assert set(axes) == {"slow", "mid", "peak"}  # no audio -> no feel
    for a in axes.values():
        assert set(a) == set(FEEL_AXES)
        assert all(0.0 <= v <= 1.0 for v in a.values())
    assert axes["slow"]["energy"] < axes["mid"]["energy"] < axes["peak"]["energy"]
    assert axes["slow"]["tempo"] < axes["peak"]["tempo"]
    assert axes["slow"]["melodic"] > axes["peak"]["melodic"]


def test_feel_bucket_names(conn) -> None:
    axes = feel_axes(conn)
    assert feel_bucket(axes["slow"]) == "deep & slow"
    assert feel_bucket(axes["peak"]) == "peak-time driving"
    assert feel_bucket(axes["mid"]) in {"rolling groove", "melodic journey"}


def test_camelot_distance() -> None:
    assert camelot_distance("8A", "8A") == 0
    assert camelot_distance("8A", "9A") == 1
    assert camelot_distance("8A", "8B") == 1
    assert camelot_distance("12A", "1A") == 1
    assert camelot_distance("8A", "2A") == 6
    assert camelot_distance("8A", None) == 3  # unknown key: neutral penalty


def test_order_for_set_sorts_by_bpm_then_harmonic_closeness(conn) -> None:
    order = order_for_set(conn, ["peak", "slow", "mid"])
    assert order == ["slow", "mid", "peak"]
    # unknown tracks go last without crashing
    assert order_for_set(conn, ["peak", "noaudio", "slow"]) == ["slow", "peak", "noaudio"]
