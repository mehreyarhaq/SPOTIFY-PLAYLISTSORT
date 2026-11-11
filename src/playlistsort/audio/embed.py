"""Audio embeddings and Discogs-style priors.

Backends share one tiny interface so the rest of the pipeline never imports torch:
  embed(path) -> float32 vector; genre_probs(path) -> {label: prob} or None.
`maest` (MTG, Discogs-400 head + embeddings) and `clap` (LAION CLAP music) load lazily through
`transformers`; `fake` exists for tests. Embeddings are stored per input kind (local file vs
preview) so the two distributions are never mixed by accident.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from playlistsort import db
from playlistsort.enrich.base import Evidence, load_tracks

PRIOR_SOURCE = "audio_discogs400"
PRIOR_MIN_PROB = 0.1
PRIOR_TOP_K = 5


class Backend(Protocol):
    name: str
    version: str
    dim: int

    def embed(self, path: Path) -> np.ndarray: ...

    def genre_probs(self, path: Path) -> Mapping[str, float] | None: ...


@dataclass
class FakeBackend:
    name: str = "fake"
    version: str = "1"
    dim: int = 8
    priors: Mapping[str, float] | None = None

    def embed(self, path: Path) -> np.ndarray:
        seed = int(hashlib.sha1(str(path).encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        return rng.standard_normal(self.dim).astype(np.float32)

    def genre_probs(self, path: Path) -> Mapping[str, float] | None:
        return dict(self.priors) if self.priors else None


def _load_audio(path: Path, sr: int, max_seconds: float | None) -> np.ndarray:
    import librosa

    y, _ = librosa.load(str(path), sr=sr, mono=True)
    if max_seconds and len(y) > int(sr * max_seconds):
        start = (len(y) - int(sr * max_seconds)) // 2  # centre of the file
        y = y[start : start + int(sr * max_seconds)]
    return y.astype(np.float32)


class MaestBackend:
    """MTG MAEST (30 s, pretrained on Discogs): embeddings + 400 Discogs style probabilities."""

    name = "maest"
    version = "mtg-upf/discogs-maest-30s-pw-129e"
    dim = 768
    sr = 16000

    def __init__(self, device: str | None = None) -> None:
        import torch
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.extractor = AutoFeatureExtractor.from_pretrained(self.version, trust_remote_code=True)
        self.model = AutoModelForAudioClassification.from_pretrained(self.version, trust_remote_code=True)
        self.model.to(self.device).eval()
        self.labels = self.model.config.id2label

    def _forward(self, path: Path):
        y = _load_audio(path, self.sr, 30.0)
        inputs = self.extractor(y, sampling_rate=self.sr, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with self.torch.no_grad():
            return self.model(**inputs, output_hidden_states=True)

    def embed(self, path: Path) -> np.ndarray:
        out = self._forward(path)
        hidden = out.hidden_states[7][0]  # layer 7, as recommended by the MAEST paper
        vec = self.torch.cat([hidden[0], hidden[1], hidden[2:].mean(dim=0)]).float().cpu().numpy()
        return vec.astype(np.float32)

    def genre_probs(self, path: Path) -> Mapping[str, float]:
        out = self._forward(path)
        probs = self.torch.sigmoid(out.logits[0]).float().cpu().numpy()
        return {self.labels[i]: float(p) for i, p in enumerate(probs)}


class ClapBackend:
    """LAION CLAP (music checkpoint) audio embeddings for similarity."""

    name = "clap"
    version = "laion/larger_clap_music"
    dim = 512
    sr = 48000

    def __init__(self, device: str | None = None) -> None:
        import torch
        from transformers import ClapModel, ClapProcessor

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.processor = ClapProcessor.from_pretrained(self.version)
        self.model = ClapModel.from_pretrained(self.version).to(self.device).eval()

    def embed(self, path: Path) -> np.ndarray:
        y = _load_audio(path, self.sr, 30.0)
        inputs = self.processor(audios=y, sampling_rate=self.sr, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with self.torch.no_grad():
            feats = self.model.get_audio_features(**inputs)
        vec = feats[0].float().cpu().numpy()
        return (vec / (np.linalg.norm(vec) + 1e-12)).astype(np.float32)

    def genre_probs(self, path: Path) -> None:
        return None


_BACKENDS: dict[str, Callable[[], Any]] = {
    "fake": FakeBackend,
    "maest": MaestBackend,
    "clap": ClapBackend,
}


def available_backends() -> list[str]:
    return list(_BACKENDS)


def get_backend(name: str) -> Backend:
    try:
        return _BACKENDS[name]()
    except KeyError as exc:
        raise ValueError(f"Unknown backend {name!r}. Choose from {', '.join(_BACKENDS)}.") from exc
    except ImportError as exc:
        raise RuntimeError(
            f"Backend {name!r} needs the `models` extra: uv sync --extra analysis --extra models"
        ) from exc


@dataclass
class EmbedStats:
    model: str
    embedded: int = 0
    already: int = 0
    no_audio: int = 0
    failed: int = 0
    prior_rows: int = 0
    errors: list[str] = field(default_factory=list)


def priors_to_evidence(track_id: str, probs: Mapping[str, float], *, input_kind: str, model: str) -> list[Evidence]:
    top = sorted(probs.items(), key=lambda kv: -kv[1])[:PRIOR_TOP_K]
    out: list[Evidence] = []
    for label, p in top:
        if p < PRIOR_MIN_PROB:
            continue
        style = label.split("---")[-1].strip()
        out.append(
            Evidence(
                track_id=track_id,
                source=PRIOR_SOURCE,
                field="style",
                raw_value=style,
                source_tag_id=label,
                match_method="id",
                match_score=round(float(p), 4),
                meta={"model": model, "input_kind": input_kind, "label": label},
            )
        )
    return out


def embed_library(
    conn: sqlite3.Connection,
    backend: Backend,
    *,
    only_missing: bool = True,
    limit: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> EmbedStats:
    stats = EmbedStats(model=backend.name)
    existing = db.get_embeddings(conn, model=backend.name, model_version=backend.version)
    tracks = load_tracks(conn)
    if limit is not None:
        tracks = tracks[:limit]
    for i, track in enumerate(tracks, 1):
        source = db.best_audio_source(conn, track.spotify_id)
        if source is None:
            stats.no_audio += 1
            continue
        if only_missing and track.spotify_id in existing:
            stats.already += 1
            continue
        path = Path(source.local_path)
        try:
            vec = backend.embed(path)
            probs = backend.genre_probs(path)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop a 2000-track run
            stats.failed += 1
            stats.errors.append(f"{track.spotify_id}: {type(exc).__name__}: {exc}")
            continue
        with conn:
            db.put_embedding(
                conn,
                track_id=track.spotify_id,
                model=backend.name,
                model_version=backend.version,
                input_kind=source.kind,
                offset_s=0.0,
                vector=vec,
            )
            if probs:
                for ev in priors_to_evidence(track.spotify_id, probs, input_kind=source.kind, model=backend.name):
                    stats.prior_rows += int(db.add_evidence(conn, ev))
        stats.embedded += 1
        if progress:
            progress(i, len(tracks))
    return stats
