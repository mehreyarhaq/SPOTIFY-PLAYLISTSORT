"""Migration 002: audio sources, features and embeddings."""

from __future__ import annotations

import numpy as np
import pytest

from playlistsort import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    with c:
        db.upsert_track(c, spotify_id="t1", title="Cola", base_title="Cola", isrc="GB1")
    return c


def test_audio_tables_exist(conn) -> None:
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"audio_sources", "features", "embeddings"} <= names
    assert db.schema_version(conn) >= 2


def test_audio_source_roundtrip(conn) -> None:
    with conn:
        db.upsert_audio_source(
            conn, track_id="t1", kind="deezer_preview", local_path="/a/b.mp3", duration_s=30.0
        )
        db.upsert_audio_source(
            conn, track_id="t1", kind="local", local_path="/music/cola.aiff", duration_s=412.1
        )
    best = db.best_audio_source(conn, "t1")
    assert best is not None and best.kind == "local" and best.local_path == "/music/cola.aiff"
    assert {s.kind for s in db.audio_sources(conn, "t1")} == {"deezer_preview", "local"}


def test_features_roundtrip(conn) -> None:
    with conn:
        db.upsert_features(
            conn,
            track_id="t1",
            input_kind="local",
            values={"bpm": 124.0, "camelot": "8A", "crest_db": 11.2},
        )
    row = db.get_features(conn, "t1")
    assert row is not None
    assert row["bpm"] == 124.0 and row["camelot"] == "8A" and row["input_kind"] == "local"
    with conn:
        db.upsert_features(conn, track_id="t1", input_kind="local", values={"bpm": 125.0})
    assert db.get_features(conn, "t1")["bpm"] == 125.0
    assert conn.execute("select count(*) from features").fetchone()[0] == 1


def test_embedding_roundtrip_preserves_dtype_and_shape(conn) -> None:
    vec = np.arange(8, dtype=np.float32) / 7
    with conn:
        db.put_embedding(
            conn,
            track_id="t1",
            model="fake",
            model_version="1",
            input_kind="deezer_preview",
            offset_s=0.0,
            vector=vec,
        )
    got = db.get_embeddings(conn, model="fake", input_kind="deezer_preview")
    assert list(got) == ["t1"]
    np.testing.assert_allclose(got["t1"], vec)
    assert got["t1"].dtype == np.float32
    # same key replaces, different input_kind is a separate row
    with conn:
        db.put_embedding(
            conn,
            track_id="t1",
            model="fake",
            model_version="1",
            input_kind="deezer_preview",
            offset_s=0.0,
            vector=vec * 2,
        )
        db.put_embedding(
            conn,
            track_id="t1",
            model="fake",
            model_version="1",
            input_kind="local",
            offset_s=0.0,
            vector=vec,
        )
    assert conn.execute("select count(*) from embeddings").fetchone()[0] == 2
    np.testing.assert_allclose(
        db.get_embeddings(conn, model="fake", input_kind="deezer_preview")["t1"], vec * 2
    )
