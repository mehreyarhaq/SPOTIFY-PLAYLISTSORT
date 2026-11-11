"""Signal features for DJ sorting, computed with librosa (no neural nets needed).

Per clip: BPM (folded into the 100-150 range DJs think in, raw kept too) with a confidence,
key + Camelot code, loudness (RMS/peak/crest), sub-bass share (< 60 Hz), harmonic share from
HPSS, tempogram peak concentration (how "four-on-the-floor" the rhythm is), a sidechain-pump
proxy (periodicity of the loudness envelope at the beat), and spectral centroid (brightness).
These are the hand-made descriptors research found to separate EDM subgenres better than
generic embeddings; they also feed the feel axes later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

SR = 22050
HOP = 512
SUB_BASS_HZ = 60.0
NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
ENHARMONIC = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
CAMELOT_MAJOR = {
    "C": "8B",
    "G": "9B",
    "D": "10B",
    "A": "11B",
    "E": "12B",
    "B": "1B",
    "F#": "2B",
    "C#": "3B",
    "G#": "4B",
    "D#": "5B",
    "A#": "6B",
    "F": "7B",
}
CAMELOT_MINOR = {
    "A": "8A",
    "E": "9A",
    "B": "10A",
    "F#": "11A",
    "C#": "12A",
    "G#": "1A",
    "D#": "2A",
    "A#": "3A",
    "F": "4A",
    "C": "5A",
    "G": "6A",
    "D": "7A",
}
# Krumhansl-Kessler key profiles
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


@dataclass(frozen=True)
class Features:
    duration_s: float
    bpm: float | None
    bpm_raw: float | None
    bpm_confidence: float
    key_tonic: str | None
    key_mode: str | None
    camelot: str | None
    key_strength: float
    rms_db: float
    peak_db: float
    crest_db: float
    sub_bass_ratio: float
    hp_ratio: float
    tempogram_peak: float
    pump: float
    spectral_centroid_hz: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def camelot_for(tonic: str | None, mode: str | None) -> str | None:
    if not tonic or not mode:
        return None
    t = ENHARMONIC.get(tonic, tonic)
    return (CAMELOT_MAJOR if mode == "major" else CAMELOT_MINOR).get(t)


def fold_bpm(bpm: float | None, lo: float = 100.0, hi: float = 150.0) -> float | None:
    """Pick the octave of `bpm` that lands in [lo, hi] when one does; otherwise keep it."""
    if bpm is None or not np.isfinite(bpm) or bpm <= 0:
        return None
    for candidate in (bpm, bpm * 2, bpm / 2):
        if lo <= candidate <= hi:
            return round(float(candidate), 2)
    return round(float(bpm), 2)


def estimate_key(chroma_mean: np.ndarray) -> tuple[str | None, str | None, float]:
    if chroma_mean is None or not np.isfinite(chroma_mean).all() or chroma_mean.sum() <= 0:
        return None, None, 0.0
    c = chroma_mean - chroma_mean.mean()
    if np.allclose(c, 0):
        return None, None, 0.0
    best = (-2.0, None, None)
    for mode, profile in (("major", _MAJOR), ("minor", _MINOR)):
        p = profile - profile.mean()
        for i in range(12):
            r = float(np.dot(c, np.roll(p, i)) / (np.linalg.norm(c) * np.linalg.norm(p) + 1e-12))
            if r > best[0]:
                best = (r, NOTES[i], mode)
    strength = float(np.clip(best[0], 0.0, 1.0))
    return best[1], best[2], strength


def _db(x: float) -> float:
    return float(20.0 * np.log10(max(float(x), 1e-12)))


def analyse_array(y: np.ndarray, sr: int = SR) -> Features:
    import librosa

    y = np.asarray(y, dtype=np.float32)
    if y.ndim > 1:
        y = librosa.to_mono(y.T if y.shape[0] > y.shape[1] else y)
    duration = len(y) / sr
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    rms = float(np.sqrt(np.mean(y**2))) if len(y) else 0.0
    rms_db, peak_db = _db(rms), _db(peak)
    silent = peak <= 1e-6 or len(y) < sr // 2

    if silent:
        return Features(
            duration_s=round(duration, 3),
            bpm=None,
            bpm_raw=None,
            bpm_confidence=0.0,
            key_tonic=None,
            key_mode=None,
            camelot=None,
            key_strength=0.0,
            rms_db=rms_db,
            peak_db=peak_db,
            crest_db=peak_db - rms_db,
            sub_bass_ratio=0.0,
            hp_ratio=0.0,
            tempogram_peak=0.0,
            pump=0.0,
            spectral_centroid_hz=0.0,
        )

    # --- spectrum: sub-bass share and brightness
    n_fft = 4096
    spec = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    total = float(spec.sum()) + 1e-12
    sub_bass_ratio = float(spec[freqs < SUB_BASS_HZ].sum() / total)
    centroid = float(np.mean(librosa.feature.spectral_centroid(S=np.sqrt(spec), sr=sr)))

    # --- harmonic / percussive share
    y_h, y_p = librosa.effects.hpss(y)
    eh, ep = float(np.sum(y_h**2)), float(np.sum(y_p**2))
    hp_ratio = eh / (eh + ep + 1e-12)

    # --- tempo
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    bpm_raw: float | None = None
    confidence = 0.0
    tempogram_peak = 0.0
    if oenv.size > 8 and np.max(oenv) > 0:
        tempo, beats = librosa.beat.beat_track(
            onset_envelope=oenv, sr=sr, hop_length=HOP, start_bpm=125.0, units="frames"
        )
        est = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
        if len(beats) >= 4:
            ibi = np.diff(librosa.frames_to_time(beats, sr=sr, hop_length=HOP))
            ibi = ibi[(ibi > 0.2) & (ibi < 2.0)]
            if ibi.size >= 3:
                est = 60.0 / float(np.mean(ibi))
        if np.isfinite(est) and est > 0:
            bpm_raw = est
            per_frame = librosa.feature.tempo(
                onset_envelope=oenv, sr=sr, hop_length=HOP, start_bpm=125.0, aggregate=None
            )
            per_frame = np.atleast_1d(per_frame).astype(float)
            if per_frame.size:
                rel = np.min(
                    np.abs(
                        np.log2(
                            np.maximum(per_frame, 1e-6)[:, None]
                            / np.array([est, est * 2, est / 2])[None, :]
                        )
                    ),
                    axis=1,
                )
                confidence = float(np.mean(rel < np.log2(1.04)))
        tg = librosa.feature.tempogram(onset_envelope=oenv, sr=sr, hop_length=HOP)
        tg_mean = np.mean(tg, axis=1)
        if tg_mean.sum() > 0:
            tempogram_peak = float(tg_mean.max() / (tg_mean.mean() + 1e-12))
    bpm = fold_bpm(bpm_raw)

    # --- sidechain pump proxy: loudness-envelope periodicity at the beat
    pump = 0.0
    if bpm:
        env = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
        env = env - env.mean()
        if np.any(env):
            ac = np.correlate(env, env, mode="full")[len(env) - 1 :]
            ac = ac / (ac[0] + 1e-12)
            lag = int(round(60.0 / bpm * sr / HOP))
            if 0 < lag < len(ac):
                pump = float(np.clip(ac[lag], 0.0, 1.0))

    # --- key
    chroma = librosa.feature.chroma_cqt(y=y_h if eh > 0 else y, sr=sr, hop_length=HOP)
    tonic, mode, strength = estimate_key(np.mean(chroma, axis=1))

    return Features(
        duration_s=round(duration, 3),
        bpm=bpm,
        bpm_raw=round(bpm_raw, 2) if bpm_raw else None,
        bpm_confidence=round(confidence, 3),
        key_tonic=tonic,
        key_mode=mode,
        camelot=camelot_for(tonic, mode),
        key_strength=round(strength, 3),
        rms_db=round(rms_db, 2),
        peak_db=round(peak_db, 2),
        crest_db=round(peak_db - rms_db, 2),
        sub_bass_ratio=round(sub_bass_ratio, 4),
        hp_ratio=round(hp_ratio, 4),
        tempogram_peak=round(tempogram_peak, 3),
        pump=round(pump, 3),
        spectral_centroid_hz=round(centroid, 1),
    )


def analyse_file(path: str | Path, *, sr: int = SR, max_seconds: float | None = None) -> Features:
    import librosa

    y, _ = librosa.load(str(path), sr=sr, mono=True, duration=max_seconds)
    return analyse_array(y, sr)
