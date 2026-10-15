"""Signal features on synthesized audio (needs the `analysis` extra)."""

from __future__ import annotations

import numpy as np
import pytest

librosa = pytest.importorskip("librosa")
sf = pytest.importorskip("soundfile")

from playlistsort.audio.features import Features, analyse_array, analyse_file, camelot_for, fold_bpm  # noqa: E402

SR = 22050


def _click_track(bpm: float, seconds: float = 20.0) -> np.ndarray:
    times = np.arange(0, seconds, 60.0 / bpm)
    clicks = librosa.clicks(
        times=times, sr=SR, length=int(SR * seconds), click_freq=1000.0, click_duration=0.05
    )
    # a kick-like low thump on every beat so there is some bass energy too
    t = np.arange(int(SR * 0.12)) / SR
    thump = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 30)
    y = clicks.copy()
    for beat in times:
        i = int(beat * SR)
        y[i : i + len(thump)] += thump[: max(0, min(len(thump), len(y) - i))]
    return (y / np.max(np.abs(y))).astype(np.float32)


def _tone(freqs, seconds=8.0) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    y = sum(np.sin(2 * np.pi * f * t) for f in freqs)
    return (y / np.max(np.abs(y))).astype(np.float32)


def test_fold_bpm_into_dj_range() -> None:
    assert fold_bpm(62) == 124
    assert fold_bpm(248) == 124
    assert fold_bpm(128) == 128
    assert fold_bpm(170) == 170  # drum & bass stays where it is
    assert fold_bpm(None) is None


def test_camelot_mapping() -> None:
    assert camelot_for("A", "major") == "11B"
    assert camelot_for("A", "minor") == "8A"
    assert camelot_for("C", "major") == "8B"
    assert camelot_for("F#", "minor") == "11A"
    assert camelot_for("Db", "major") == "3B"


def test_bpm_of_a_click_track() -> None:
    f = analyse_array(_click_track(124.0), SR)
    assert isinstance(f, Features)
    assert f.bpm is not None and abs(f.bpm - 124.0) <= 1.5
    assert 0.0 <= f.bpm_confidence <= 1.0


def test_slow_click_track_is_folded_up() -> None:
    f = analyse_array(_click_track(62.0), SR)
    assert f.bpm is not None and abs(f.bpm - 124.0) <= 1.5


def test_sub_bass_ratio_separates_bass_from_treble() -> None:
    bass = analyse_array(_tone([50.0]), SR)
    treble = analyse_array(_tone([2000.0]), SR)
    assert bass.sub_bass_ratio > 0.8
    assert treble.sub_bass_ratio < 0.05


def test_key_of_an_a_major_chord() -> None:
    f = analyse_array(_tone([440.0, 554.37, 659.25, 880.0]), SR)
    assert f.key_tonic == "A"
    assert f.key_mode == "major"
    assert f.camelot == "11B"


def test_silence_is_handled() -> None:
    f = analyse_array(np.zeros(SR * 5, dtype=np.float32), SR)
    assert f.duration_s == pytest.approx(5.0, abs=0.01)
    assert f.key_tonic is None
    assert np.isfinite(f.rms_db) or f.rms_db == -np.inf or f.rms_db is None


def test_analyse_file_roundtrip(tmp_path) -> None:
    path = tmp_path / "click.wav"
    sf.write(path, _click_track(126.0, seconds=12.0), SR)
    f = analyse_file(path)
    assert f.duration_s == pytest.approx(12.0, abs=0.05)
    assert f.bpm is not None and abs(f.bpm - 126.0) <= 1.5
    d = f.as_dict()
    assert set(d) >= {
        "bpm",
        "camelot",
        "rms_db",
        "crest_db",
        "sub_bass_ratio",
        "hp_ratio",
        "tempogram_peak",
        "pump",
    }
