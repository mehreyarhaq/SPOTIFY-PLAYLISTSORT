"""Embedding backends and the library embedding loop (fake backend; no models downloaded)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from playlistsort import db
from playlistsort.audio.embed import FakeBackend, available_backends, embed_library, get_backend
from playlistsort.model.scorer import load_weights, score_library
from playlistsort.taxonomy.normalize import load_taxonomy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(":memory:")
    db.migrate(c)
    with c:
        db.upsert_track(c, spotify_id="t1", title="Cola", base_title="Cola")
        db.upsert_track(c, spotify_id="t2", title="Rej", base_title="Rej")
        db.upsert_track(c, spotify_id="t3", title="No audio", base_title="No audio")
        for tid, kind in (("t1", "local"), ("t2", "deezer_preview")):
            p = tmp_path / f"{tid}.wav"
            p.write_bytes(b"")
            db.upsert_audio_source(c, track_id=tid, kind=kind, local_path=str(p), duration_s=30.0)
    return c


def test_fake_backend_is_registered_and_deterministic() -> None:
    assert "fake" in available_backends()
    b = get_backend("fake")
    assert isinstance(b, FakeBackend)
    v1, v2 = b.embed(Path("/x/a.wav")), b.embed(Path("/x/a.wav"))
    np.testing.assert_array_equal(v1, v2)
    assert v1.shape == (b.dim,) and v1.dtype == np.float32


def test_embed_library_stores_vectors_per_input_kind_and_priors_as_evidence(conn) -> None:
    backend = FakeBackend(
        priors={
            "Electronic---Tech House": 0.7,
            "Electronic---Deep House": 0.2,
            "Rock---Indie Rock": 0.05,
        }
    )
    stats = embed_library(conn, backend)
    assert stats.embedded == 2 and stats.no_audio == 1
    local = db.get_embeddings(conn, model="fake", input_kind="local")
    preview = db.get_embeddings(conn, model="fake", input_kind="deezer_preview")
    assert list(local) == ["t1"] and list(preview) == ["t2"]
    rows = conn.execute(
        "select track_id, raw_value, match_score from evidence"
        " where source='audio_discogs400' order by track_id, raw_value"
    ).fetchall()
    assert [(r["track_id"], r["raw_value"], r["match_score"]) for r in rows] == [
        ("t1", "Deep House", 0.2),
        ("t1", "Tech House", 0.7),
        ("t2", "Deep House", 0.2),
        ("t2", "Tech House", 0.7),
    ]
    # the scorer scales audio priors by their probability
    tax = load_taxonomy(ROOT / "config" / "taxonomy.yaml")
    weights = load_weights(ROOT / "config" / "weights.yaml")
    s = score_library(conn, taxonomy=tax, weights=weights)["t1"]
    assert s.scores["tech_house"] == pytest.approx(0.5 * 0.7, abs=1e-6)
    assert s.scores["deep_house"] == pytest.approx(0.5 * 0.2, abs=1e-6)


def test_embed_library_skips_existing_unless_forced(conn) -> None:
    backend = FakeBackend()
    embed_library(conn, backend)
    again = embed_library(conn, backend)
    assert again.embedded == 0 and again.already == 2
    forced = embed_library(conn, backend, only_missing=False)
    assert forced.embedded == 2
