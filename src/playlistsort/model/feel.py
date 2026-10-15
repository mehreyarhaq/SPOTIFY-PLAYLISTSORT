"""Feel: a handful of interpretable axes DJs actually use, computed from the signal features.

Each axis is a library-relative percentile in [0, 1], so "energy 0.9" means louder, denser and
more pumping than 90% of this user's library (previews bias energy upward, so relative is the
honest scale). Buckets give the first feel playlists names before the user has labelled any;
feel labels then train the same classifier on axis="feel".

  energy   : loudness, low crest factor, pump, tempogram peak (dense four-to-the-floor)
  tempo    : BPM
  melodic  : harmonic share (vs percussive)
  bright   : spectral centroid
  bass     : sub-bass share
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

import numpy as np

from playlistsort import db

FEEL_AXES = ("energy", "tempo", "melodic", "bright", "bass")
FEEL_BUCKETS = ("deep & slow", "rolling groove", "melodic journey", "peak-time driving")

_RAW = {
    "energy": lambda f: (
        0.4 * f["rms_db"]
        - 0.2 * f["crest_db"]
        + 8.0 * f["pump"]
        + 1.5 * f["tempogram_peak"]
        + 20.0 * f["sub_bass_ratio"]
    ),
    "tempo": lambda f: f["bpm"],
    "melodic": lambda f: f["hp_ratio"],
    "bright": lambda f: f["spectral_centroid_hz"],
    "bass": lambda f: f["sub_bass_ratio"],
}
_NEEDED = (
    "bpm",
    "rms_db",
    "crest_db",
    "pump",
    "tempogram_peak",
    "sub_bass_ratio",
    "hp_ratio",
    "spectral_centroid_hz",
)


def _percentiles(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ids = list(values)
    arr = np.array([values[i] for i in ids], dtype=float)
    ranks = arr.argsort().argsort().astype(float)
    n = max(1, len(arr) - 1)
    return {i: float(r / n) for i, r in zip(ids, ranks, strict=True)}


def feel_axes(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    feats = db.all_features(conn)
    usable = {tid: f for tid, f in feats.items() if all(f.get(k) is not None for k in _NEEDED)}
    out: dict[str, dict[str, float]] = {tid: {} for tid in usable}
    for axis, fn in _RAW.items():
        raw = {tid: float(fn(f)) for tid, f in usable.items()}
        for tid, p in _percentiles(raw).items():
            out[tid][axis] = p
    return out


def feel_bucket(axes: dict[str, float]) -> str:
    e, t, m = axes.get("energy", 0.5), axes.get("tempo", 0.5), axes.get("melodic", 0.5)
    if e >= 0.66 and t >= 0.5:
        return "peak-time driving"
    if e <= 0.34 and t <= 0.5:
        return "deep & slow"
    return "melodic journey" if m >= 0.5 else "rolling groove"


# --------------------------------------------------------------------------- set ordering


def _parse_camelot(code: str | None) -> tuple[int, str] | None:
    if not code or len(code) < 2:
        return None
    try:
        return int(code[:-1]), code[-1].upper()
    except ValueError:
        return None


def camelot_distance(a: str | None, b: str | None) -> int:
    """Steps around the Camelot wheel (0 = same key, 1 = mixable neighbour); 3 when unknown."""
    pa, pb = _parse_camelot(a), _parse_camelot(b)
    if pa is None or pb is None:
        return 3
    (na, la), (nb, lb) = pa, pb
    around = min((na - nb) % 12, (nb - na) % 12)
    return around + (0 if la == lb else 1)


def order_for_set(conn: sqlite3.Connection, track_ids: Sequence[str]) -> list[str]:
    """Ascending BPM; among near-equal BPMs prefer harmonic neighbours; unknown BPM last."""
    feats = db.all_features(conn)
    known = [t for t in track_ids if feats.get(t, {}).get("bpm")]
    unknown = [t for t in track_ids if t not in set(known)]
    known.sort(key=lambda t: feats[t]["bpm"])
    ordered: list[str] = []
    remaining = list(known)
    while remaining:
        if not ordered:
            ordered.append(remaining.pop(0))
            continue
        prev = feats[ordered[-1]]
        window = [t for t in remaining if feats[t]["bpm"] - feats[remaining[0]]["bpm"] <= 1.5]
        nxt = min(
            window,
            key=lambda t: (
                camelot_distance(prev.get("camelot"), feats[t].get("camelot")),
                feats[t]["bpm"],
            ),
        )
        remaining.remove(nxt)
        ordered.append(nxt)
    return ordered + unknown
