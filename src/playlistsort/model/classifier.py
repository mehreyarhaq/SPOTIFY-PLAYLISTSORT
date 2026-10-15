"""The personalised classifier: the user's labels decide what each playlist means.

Inputs per track: audio embeddings (one block per model, zero-filled with an indicator when a
track has no audio yet), standardised signal features (BPM, loudness, sub-bass, ...), and the
evidence prior over genres from the scorer. Model: multinomial logistic regression with
sample weights, so "between A and B" labels become two half-weight examples. Evaluation uses
GroupKFold by primary artist so the score is not inflated by memorising artists.

User labels are honoured verbatim at prediction time: a seed label pins the track, a between
label splits it, a negative label zeroes that category.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from playlistsort import db
from playlistsort.model.scorer import TrackScore, Weights, score_library
from playlistsort.taxonomy.normalize import Taxonomy

SIGNAL_FEATURES = (
    "bpm",
    "bpm_confidence",
    "rms_db",
    "crest_db",
    "sub_bass_ratio",
    "hp_ratio",
    "tempogram_peak",
    "pump",
    "spectral_centroid_hz",
)
MIN_LABELS_PER_CATEGORY = 3
MIN_CATEGORIES = 2


class NotEnoughLabelsError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeatureMatrix:
    track_ids: list[str]
    x: np.ndarray
    groups: list[str]
    columns: list[str]
    prior_columns: list[str]
    n_signal_features: int
    scores: Mapping[str, TrackScore]


def _primary_artist(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "select track_id, artist_id from track_artists where position = 0"
        " union select track_id, min(artist_id) from track_artists group by track_id"
    ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(r["track_id"], r["artist_id"])
    return out


def build_feature_matrix(
    conn: sqlite3.Connection,
    *,
    taxonomy: Taxonomy,
    weights: Weights,
    embedding_models: Sequence[str] = ("maest", "clap"),
    scores: Mapping[str, TrackScore] | None = None,
) -> FeatureMatrix:
    scores = (
        scores if scores is not None else score_library(conn, taxonomy=taxonomy, weights=weights)
    )
    track_ids = [
        r["spotify_id"]
        for r in conn.execute("select spotify_id from tracks where is_local=0 order by spotify_id")
    ]
    blocks: list[np.ndarray] = []
    columns: list[str] = []

    for model in embedding_models:
        vectors = db.get_embeddings(conn, model=model)
        dim = next((v.size for v in vectors.values()), 0)
        if dim == 0:
            continue
        block = np.zeros((len(track_ids), dim + 1), dtype=np.float32)
        for i, tid in enumerate(track_ids):
            v = vectors.get(tid)
            if v is not None and v.size == dim:
                block[i, :dim] = v
                block[i, dim] = 1.0
        blocks.append(block)
        columns += [f"{model}_{j}" for j in range(dim)] + [f"{model}_present"]

    feats = db.all_features(conn)
    sig = np.zeros((len(track_ids), len(SIGNAL_FEATURES) + 1), dtype=np.float32)
    for i, tid in enumerate(track_ids):
        f = feats.get(tid)
        if not f:
            continue
        sig[i, -1] = 1.0
        for j, name in enumerate(SIGNAL_FEATURES):
            value = f.get(name)
            sig[i, j] = float(value) if value is not None and np.isfinite(float(value)) else 0.0
    present = sig[:, -1] > 0
    if present.any():
        mu = sig[present, :-1].mean(axis=0)
        sd = sig[present, :-1].std(axis=0) + 1e-6
        sig[present, :-1] = (sig[present, :-1] - mu) / sd
    blocks.append(sig)
    columns += [f"sig_{n}" for n in SIGNAL_FEATURES] + ["sig_present"]

    prior_columns = [g for g in taxonomy.genres]
    prior = np.zeros((len(track_ids), len(prior_columns)), dtype=np.float32)
    col = {g: j for j, g in enumerate(prior_columns)}
    for i, tid in enumerate(track_ids):
        s = scores.get(tid)
        if s:
            for tag, p in s.probs.items():
                if tag in col:
                    prior[i, col[tag]] = p
    blocks.append(prior)
    columns += [f"prior_{g}" for g in prior_columns]

    x = np.hstack(blocks) if blocks else np.zeros((len(track_ids), 0), dtype=np.float32)
    artists = _primary_artist(conn)
    groups = [artists.get(tid, tid) for tid in track_ids]
    return FeatureMatrix(
        track_ids=track_ids,
        x=x,
        groups=groups,
        columns=columns,
        prior_columns=prior_columns,
        n_signal_features=len(SIGNAL_FEATURES),
        scores=scores,
    )


@dataclass
class TrainedModel:
    axis: str
    categories: list[str]
    embedding_models: tuple[str, ...]
    columns: list[str]
    estimator: Any
    trained_at: str = ""


@dataclass
class TrainReport:
    n_labels: int
    n_tracks: int
    categories: list[str]
    cv_accuracy: float
    cv_folds: int
    per_category: dict[str, dict[str, float]] = field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"{self.n_labels} labels on {self.n_tracks} tracks, {len(self.categories)} categories,"
            f" grouped {self.cv_folds}-fold CV accuracy {self.cv_accuracy:.2f}"
        ]
        for cat, m in self.per_category.items():
            lines.append(
                f"  {cat}: precision {m['precision']:.2f} recall {m['recall']:.2f}"
                f" (n={int(m['support'])})"
            )
        return "\n".join(lines)


def _training_rows(conn: sqlite3.Connection, axis: str) -> list[tuple[str, str, float]]:
    rows: list[tuple[str, str, float]] = []
    for tid, labels in db.latest_labels(conn, axis=axis).items():
        for cat, w, kind in labels:
            if kind != "negative" and w > 0:
                rows.append((tid, cat, w))
    return rows


def train(
    conn: sqlite3.Connection,
    *,
    axis: str,
    taxonomy: Taxonomy,
    weights: Weights,
    embedding_models: Sequence[str] = ("maest", "clap"),
    c_reg: float = 1.0,
) -> tuple[TrainedModel, TrainReport]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold

    fm = build_feature_matrix(
        conn, taxonomy=taxonomy, weights=weights, embedding_models=embedding_models
    )
    index = {tid: i for i, tid in enumerate(fm.track_ids)}
    rows = [(tid, cat, w) for tid, cat, w in _training_rows(conn, axis) if tid in index]
    counts: dict[str, float] = {}
    for _tid, cat, w in rows:
        counts[cat] = counts.get(cat, 0.0) + w
    categories = sorted(c for c, n in counts.items() if n >= MIN_LABELS_PER_CATEGORY)
    if len(categories) < MIN_CATEGORIES:
        raise NotEnoughLabelsError(
            f"Need at least {MIN_CATEGORIES} categories with {MIN_LABELS_PER_CATEGORY}+ labels"
            " each;"
            f" have {dict(counts)}. Label more tracks (playlistsort labels export/import)."
        )
    rows = [r for r in rows if r[1] in categories]
    x_train = fm.x[[index[tid] for tid, _c, _w in rows]]
    ytr = np.array([cat for _t, cat, _w in rows])
    wtr = np.array([w for _t, _c, w in rows], dtype=float)
    gtr = np.array([fm.groups[index[tid]] for tid, _c, _w in rows])

    def make() -> LogisticRegression:
        return LogisticRegression(C=c_reg, max_iter=2000, class_weight="balanced")

    n_groups = len(set(gtr))
    folds = max(2, min(5, n_groups))
    confusion = {a: dict.fromkeys(categories, 0) for a in categories}
    correct = total = 0
    if n_groups >= 2:
        for tr_idx, te_idx in GroupKFold(n_splits=folds).split(x_train, ytr, gtr):
            if len(set(ytr[tr_idx])) < 2:
                continue
            est = make().fit(x_train[tr_idx], ytr[tr_idx], sample_weight=wtr[tr_idx])
            for truth, pred in zip(ytr[te_idx], est.predict(x_train[te_idx]), strict=True):
                confusion[truth][pred] += 1
                correct += int(truth == pred)
                total += 1
    per_category: dict[str, dict[str, float]] = {}
    for cat in categories:
        tp = confusion[cat][cat]
        support = sum(confusion[cat].values())
        predicted = sum(confusion[other][cat] for other in categories)
        per_category[cat] = {
            "precision": tp / predicted if predicted else 0.0,
            "recall": tp / support if support else 0.0,
            "support": float(support),
        }
    estimator = make().fit(x_train, ytr, sample_weight=wtr)
    model = TrainedModel(
        axis=axis,
        categories=list(estimator.classes_),
        embedding_models=tuple(embedding_models),
        columns=fm.columns,
        estimator=estimator,
    )
    report = TrainReport(
        n_labels=len(rows),
        n_tracks=len({tid for tid, _c, _w in rows}),
        categories=categories,
        cv_accuracy=(correct / total) if total else 0.0,
        cv_folds=folds,
        per_category=per_category,
        confusion=confusion,
    )
    return model, report


def predict_library(
    conn: sqlite3.Connection,
    model: TrainedModel,
    *,
    taxonomy: Taxonomy,
    weights: Weights,
    scores: Mapping[str, TrackScore] | None = None,
) -> dict[str, dict[str, float]]:
    """Category probabilities for every track, with the user's own labels honoured verbatim."""
    fm = build_feature_matrix(
        conn,
        taxonomy=taxonomy,
        weights=weights,
        embedding_models=model.embedding_models,
        scores=scores,
    )
    if fm.columns != model.columns:
        raise RuntimeError(
            "Feature layout changed since training (new embeddings?). Run `train` again."
        )
    probs = model.estimator.predict_proba(fm.x)
    cats = list(model.estimator.classes_)
    out: dict[str, dict[str, float]] = {
        tid: {c: float(p) for c, p in zip(cats, row, strict=True)}
        for tid, row in zip(fm.track_ids, probs, strict=True)
    }
    for tid, labels in db.latest_labels(conn, axis=model.axis).items():
        if tid not in out:
            continue
        positives = {c: w for c, w, kind in labels if kind != "negative"}
        negatives = {c for c, _w, kind in labels if kind == "negative"}
        if positives:
            total = sum(positives.values()) or 1.0
            out[tid] = {c: positives.get(c, 0.0) / total for c in set(cats) | set(positives)}
        if negatives:
            for c in negatives:
                out[tid][c] = 0.0
            s = sum(out[tid].values())
            if s > 0:
                out[tid] = {c: p / s for c, p in out[tid].items()}
    return out


def save_model(model: TrainedModel, path: str | Path) -> None:
    import joblib

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load_model(path: str | Path) -> TrainedModel:
    import joblib

    return joblib.load(path)
