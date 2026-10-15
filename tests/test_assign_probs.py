"""Assignment from a plain probability mapping (model output)."""

from __future__ import annotations

from pathlib import Path

from playlistsort.model.assign import assign_from_probs
from playlistsort.model.scorer import load_weights
from playlistsort.taxonomy.normalize import load_taxonomy

ROOT = Path(__file__).resolve().parents[1]
TAX = load_taxonomy(ROOT / "config" / "taxonomy.yaml")
W = load_weights(ROOT / "config" / "weights.yaml")


def _k(assigns):
    return [(a.category, a.kind) for a in assigns]


def test_clear_winner() -> None:
    assert _k(
        assign_from_probs({"tech_house": 0.8, "deep_house": 0.2}, weights=W, taxonomy=TAX)
    ) == [("tech_house", "primary")]


def test_between_adds_runner_up() -> None:
    out = assign_from_probs(
        {"tech_house": 0.5, "deep_house": 0.42, "house": 0.08}, weights=W, taxonomy=TAX
    )
    assert _k(out) == [("tech_house", "primary"), ("deep_house", "secondary")]


def test_subgenre_category_also_fills_its_parent_playlist() -> None:
    out = assign_from_probs(
        {"minimal_deep_tech.deep_tech": 0.9, "tech_house": 0.1}, weights=W, taxonomy=TAX
    )
    assert _k(out) == [("minimal_deep_tech.deep_tech", "primary"), ("minimal_deep_tech", "parent")]


def test_below_threshold_is_empty() -> None:
    assert assign_from_probs({"a": 0.3, "b": 0.3, "c": 0.4}, weights=W, taxonomy=TAX) == []
    assert assign_from_probs({}, weights=W, taxonomy=TAX) == []
