"""Personalised classifier on a synthetic two-cluster library (needs the `ml` extra)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("sklearn")

from playlistsort import db  # noqa: E402
from playlistsort.model.classifier import (  # noqa: E402
    NotEnoughLabelsError,
    build_feature_matrix,
    load_model,
    predict_library,
    save_model,
    train,
)
from playlistsort.model.scorer import load_weights  # noqa: E402
from playlistsort.taxonomy.normalize import load_taxonomy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RNG = np.random.default_rng(0)


@pytest.fixture(scope="module")
def tax():
    return load_taxonomy(ROOT / "config" / "taxonomy.yaml")


@pytest.fixture(scope="module")
def weights():
    return load_weights(ROOT / "config" / "weights.yaml")


def _library(n_per_cluster: int = 20, labelled_per_cluster: int = 6):
    """Two audio clusters: tech house (fast, bass-heavy) vs deep house (slower, softer)."""
    conn = db.connect(":memory:")
    db.migrate(conn)
    centres = {"tech_house": np.full(8, 1.0), "deep_house": np.full(8, -1.0)}
    with conn:
        i = 0
        for cat, centre in centres.items():
            for k in range(n_per_cluster):
                tid = f"{cat[:4]}{k:03d}"
                artist = f"art{i % 7}"  # several tracks per artist, so GroupKFold has groups
                db.upsert_track(
                    conn,
                    spotify_id=tid,
                    title=tid,
                    base_title=tid,
                    added_at=f"2026-01-{i % 28 + 1:02d}",
                )
                db.upsert_artist(conn, spotify_id=artist, name=artist)
                db.link_track_artist(conn, track_id=tid, artist_id=artist)
                db.put_embedding(
                    conn,
                    track_id=tid,
                    model="fake",
                    model_version="1",
                    input_kind="local",
                    offset_s=0.0,
                    vector=centre + RNG.normal(0, 0.3, 8),
                )
                db.upsert_features(
                    conn,
                    track_id=tid,
                    input_kind="local",
                    values={
                        "bpm": (126 if cat == "tech_house" else 120) + RNG.normal(0, 1),
                        "sub_bass_ratio": (0.3 if cat == "tech_house" else 0.15)
                        + RNG.normal(0, 0.02),
                        "rms_db": -12.0,
                        "crest_db": 10.0,
                        "hp_ratio": 0.5,
                        "tempogram_peak": 3.0,
                        "pump": 0.4,
                        "spectral_centroid_hz": 2000.0,
                        "bpm_confidence": 0.9,
                    },
                )
                if k < labelled_per_cluster:
                    db.add_label(conn, session_id="s1", track_id=tid, axis="genre", category=cat)
                i += 1
    return conn


def test_feature_matrix_concatenates_embeddings_features_and_priors(tax, weights) -> None:
    conn = _library()
    fm = build_feature_matrix(conn, taxonomy=tax, weights=weights, embedding_models=("fake",))
    assert len(fm.track_ids) == 40
    assert fm.x.shape[0] == 40
    assert fm.x.shape[1] == 8 + 1 + fm.n_signal_features + 1 + len(fm.prior_columns)
    assert set(fm.groups) and len(set(fm.groups)) <= 7
    assert np.isfinite(fm.x).all()


def test_train_reports_grouped_cv_and_predicts_unlabelled_tracks(tax, weights) -> None:
    conn = _library()
    model, report = train(
        conn, axis="genre", taxonomy=tax, weights=weights, embedding_models=("fake",)
    )
    assert set(model.categories) == {"tech_house", "deep_house"}
    assert report.n_labels == 12
    assert report.cv_accuracy >= 0.8
    assert report.confusion["tech_house"]["tech_house"] >= 5
    preds = predict_library(conn, model, taxonomy=tax, weights=weights)
    assert len(preds) == 40
    for tid, probs in preds.items():
        expected = "tech_house" if tid.startswith("tech") else "deep_house"
        assert max(probs, key=probs.get) == expected, (tid, probs)
        assert abs(sum(probs.values()) - 1.0) < 1e-6


def test_user_labels_override_predictions_and_negatives_zero_a_category(tax, weights) -> None:
    conn = _library()
    with conn:
        db.add_label(
            conn,
            session_id="s2",
            track_id="tech010",
            axis="genre",
            category="deep_house",
            weight=0.5,
            kind="between",
        )
        db.add_label(
            conn,
            session_id="s2",
            track_id="tech010",
            axis="genre",
            category="tech_house",
            weight=0.5,
            kind="between",
        )
        db.add_label(
            conn,
            session_id="s3",
            track_id="tech011",
            axis="genre",
            category="tech_house",
            kind="negative",
        )
    model, _ = train(conn, axis="genre", taxonomy=tax, weights=weights, embedding_models=("fake",))
    preds = predict_library(conn, model, taxonomy=tax, weights=weights)
    assert preds["tech010"]["deep_house"] == pytest.approx(0.5) and preds["tech010"][
        "tech_house"
    ] == pytest.approx(0.5)
    assert preds["tech011"]["tech_house"] == 0.0
    assert preds["tech011"]["deep_house"] == pytest.approx(1.0)


def test_too_few_labels_raises(tax, weights) -> None:
    conn = _library(labelled_per_cluster=1)
    with pytest.raises(NotEnoughLabelsError):
        train(conn, axis="genre", taxonomy=tax, weights=weights, embedding_models=("fake",))


def test_model_roundtrip(tax, weights, tmp_path) -> None:
    conn = _library()
    model, _ = train(conn, axis="genre", taxonomy=tax, weights=weights, embedding_models=("fake",))
    path = tmp_path / "genre.joblib"
    save_model(model, path)
    loaded = load_model(path)
    assert loaded.categories == model.categories
    a = predict_library(conn, model, taxonomy=tax, weights=weights)
    b = predict_library(conn, loaded, taxonomy=tax, weights=weights)
    assert a["tech015"] == pytest.approx(b["tech015"])
