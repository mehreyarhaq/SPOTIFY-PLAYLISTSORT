"""assign -> publish -> publish again, offline, against a fake Spotify."""

from __future__ import annotations

from pathlib import Path

import pytest

from playlistsort import db
from playlistsort.config import Settings
from playlistsort.pipeline import NoAssignmentsError, assign_run, publish_query, publish_run
from tests.fake_spotify import FakeSpotify

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(playlistsort_data_dir=tmp_path / "data", spotify_client_id="x")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    with c:
        for tid, genre in [("a", "Tech House"), ("b", "Tech House"), ("c", "Deep House")]:
            db.upsert_track(
                c, spotify_id=tid, title=tid, base_title=tid, added_at=f"2026-01-0{ord(tid) - 96}"
            )
            db.add_evidence(c, db.Evidence(tid, "beatport", "genre", genre, match_method="isrc"))
        db.upsert_track(c, spotify_id="spotify:local:x", title="local", is_local=True)
        db.upsert_features(
            c,
            track_id="a",
            input_kind="local",
            values={
                "bpm": 128,
                "rms_db": -8,
                "crest_db": 8,
                "sub_bass_ratio": 0.3,
                "hp_ratio": 0.3,
                "tempogram_peak": 4,
                "pump": 0.6,
                "spectral_centroid_hz": 3500,
                "camelot": "8A",
            },
        )
        db.upsert_features(
            c,
            track_id="c",
            input_kind="local",
            values={
                "bpm": 118,
                "rms_db": -16,
                "crest_db": 12,
                "sub_bass_ratio": 0.1,
                "hp_ratio": 0.7,
                "tempogram_peak": 2,
                "pump": 0.2,
                "spectral_centroid_hz": 1500,
                "camelot": "5A",
            },
        )
    return c


def test_assign_run_from_evidence(conn, settings) -> None:
    result = assign_run(conn, settings, axis="genre", run_id="r1")
    assert result.source == "evidence"
    assert result.per_category == {"tech_house": 2, "deep_house": 1}
    assert result.assigned_tracks == 3


def test_assign_run_feel_uses_buckets_without_a_model(conn, settings) -> None:
    result = assign_run(conn, settings, axis="feel", run_id="f1")
    assert result.source == "feel-buckets"
    assert set(result.per_category) == {"peak-time driving", "deep & slow"}


def test_publish_run_is_idempotent(conn, settings) -> None:
    sp = FakeSpotify()
    with pytest.raises(NoAssignmentsError):
        publish_run(conn, sp, settings, axis="genre")
    assign_run(conn, settings, axis="genre", run_id="r1")
    rows = publish_run(conn, sp, settings, axis="genre")
    names = {r.name for r in rows}
    assert names == {"[PS] Tech House", "[PS] Deep House"}
    assert all(p["public"] is False for p in sp.playlists.values())
    th = db.get_playlist(conn, axis="genre", category="tech_house")
    assert sp.items[th.spotify_id] == ["spotify:track:a", "spotify:track:b"]
    before = list(sp.calls)
    rows2 = publish_run(conn, sp, settings, axis="genre")
    assert all(r.summary == "unchanged" for r in rows2)
    assert sp.calls == before  # no writes the second time


def test_publish_run_recreates_a_deleted_playlist(conn, settings) -> None:
    sp = FakeSpotify()
    assign_run(conn, settings, axis="genre", run_id="r1")
    publish_run(conn, sp, settings, axis="genre")
    th = db.get_playlist(conn, axis="genre", category="tech_house")
    sp.current_user_unfollow_playlist(th.spotify_id)
    publish_run(conn, sp, settings, axis="genre")
    th2 = db.get_playlist(conn, axis="genre", category="tech_house")
    assert th2.spotify_id != th.spotify_id
    assert sp.items[th2.spotify_id] == ["spotify:track:a", "spotify:track:b"]


def test_publish_query_builds_a_named_playlist_in_mix_order(conn, settings) -> None:
    sp = FakeSpotify()
    assign_run(conn, settings, axis="genre", run_id="r1")
    diff = publish_query(conn, sp, settings, genre="tech house", feel=None, name="Friday warmup")
    assert diff.name == "[PS] Friday warmup"
    row = db.get_playlist(conn, axis="query", category="friday warmup")
    assert sp.items[row.spotify_id] == [
        "spotify:track:a",
        "spotify:track:b",
    ]  # a has BPM, b unknown -> last
    dry = publish_query(
        conn, sp, settings, genre="tech house", feel=None, name="Friday warmup", dry_run=True
    )
    assert dry.changed is False
