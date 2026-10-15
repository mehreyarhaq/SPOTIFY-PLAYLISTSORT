"""Evidence -> genre probabilities (pure functions, real config files)."""

from __future__ import annotations

from pathlib import Path

import pytest

from playlistsort.model.scorer import EvidenceRow, Weights, load_weights, score_track
from playlistsort.taxonomy.normalize import load_taxonomy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tax():
    return load_taxonomy(ROOT / "config" / "taxonomy.yaml")


@pytest.fixture(scope="module")
def weights() -> Weights:
    return load_weights(ROOT / "config" / "weights.yaml")


def ev(source, field, raw, *, method="isrc", tag_id=None, meta=None, score=1.0) -> EvidenceRow:
    return EvidenceRow(
        track_id="t",
        source=source,
        field=field,
        raw_value=raw,
        source_tag_id=tag_id,
        match_method=method,
        match_score=score,
        meta=meta or {},
    )


def test_single_beatport_genre_is_certain(tax, weights) -> None:
    s = score_track(
        "t", [ev("beatport", "genre", "Tech House", tag_id="11")], taxonomy=tax, weights=weights
    )
    assert s.status == "ok"
    assert s.top == ("tech_house", pytest.approx(1.0))
    assert s.total_weight == pytest.approx(1.0)


def test_subgenre_credits_its_parent_and_is_recoverable(tax, weights) -> None:
    rows = [
        ev("beatport", "genre", "Minimal / Deep Tech", tag_id="14"),
        ev("beatport", "subgenre", "Deep Tech", tag_id="188"),
    ]
    s = score_track("t", rows, taxonomy=tax, weights=weights)
    assert s.top[0] == "minimal_deep_tech"
    assert s.top_subgenre("minimal_deep_tech") == (
        "minimal_deep_tech.deep_tech",
        pytest.approx(1.0),
    )
    assert s.probs["minimal_deep_tech"] == pytest.approx(1.0)


def test_sources_are_weighted_and_fuzzy_matches_capped(tax, weights) -> None:
    rows = [
        ev("beatport", "genre", "Tech House", tag_id="11"),  # 1.0
        ev("discogs", "style", "Deep House", method="fuzzy"),  # 0.7 * 0.8 = 0.56
    ]
    s = score_track("t", rows, taxonomy=tax, weights=weights)
    assert s.probs["tech_house"] == pytest.approx(1.0 / 1.56, abs=1e-3)
    assert s.probs["deep_house"] == pytest.approx(0.56 / 1.56, abs=1e-3)
    assert s.status == "ok"


def test_close_call_is_flagged_between(tax, weights) -> None:
    rows = [
        ev("beatport", "genre", "Tech House", tag_id="11"),  # 1.0
        ev("sonovault", "genre", "Deep House", method="id"),  # 0.8
    ]
    s = score_track("t", rows, taxonomy=tax, weights=weights)
    assert s.status == "between"
    assert s.top[0] == "tech_house" and s.second[0] == "deep_house"
    assert s.margin < weights.review_margin


def test_release_level_style_is_down_weighted_for_remixes_by_others(tax, weights) -> None:
    row = ev(
        "discogs",
        "style",
        "Deep House",
        method="fuzzy",
        meta={"release_level": True, "remix_by_other": True},
    )
    plain = score_track("t", [row], taxonomy=tax, weights=weights, is_remix=False)
    remix = score_track("t", [row], taxonomy=tax, weights=weights, is_remix=True)
    assert plain.total_weight == pytest.approx(0.56, abs=1e-3)
    assert remix.total_weight == pytest.approx(0.56 * 0.6, abs=1e-3)


def test_broad_tag_alone_is_unknown_but_leaves_a_family_hint(tax, weights) -> None:
    s = score_track(
        "t",
        [ev("spotify_artist", "genre", "electronic", method="id")],
        taxonomy=tax,
        weights=weights,
    )
    assert s.status == "unknown"
    assert s.family_hint == "electronic"
    assert s.top is None


def test_broad_tag_breaks_ties_toward_its_family(tax, weights) -> None:
    rows = [
        ev("discogs", "style", "Deep House", method="fuzzy"),
        ev("discogs", "style", "Techno", method="fuzzy"),  # alias -> family techno (broad)
        ev("discogs", "style", "Dub Techno", method="fuzzy"),  # techno_raw.dub
    ]
    s = score_track("t", rows, taxonomy=tax, weights=weights)
    assert s.top[0] == "techno_raw"


def test_unmapped_and_non_genre_fields_are_ignored(tax, weights) -> None:
    rows = [
        ev("beatport", "bpm", "124"),
        ev("beatport", "label", "Defected"),
        ev("discogs", "style", "Zouk Bass", method="fuzzy"),
    ]
    s = score_track("t", rows, taxonomy=tax, weights=weights)
    assert s.status == "unknown"
    assert s.total_weight == 0


def test_split_alias_lands_between_its_targets(tax, weights) -> None:
    s = score_track(
        "t", [ev("lastfm", "tag", "afro tech", method="id")], taxonomy=tax, weights=weights
    )
    assert {s.top[0], s.second[0]} == {"afro_house", "tech_house"}
    assert s.status == "between"


def test_weights_file_exposes_assignment_knobs(weights) -> None:
    assert 0 < weights.threshold < 1
    assert 0 < weights.review_margin < 1
    assert weights.min_evidence_weight > 0
    assert weights.source_weight("beatport") == 1.0
    assert weights.source_weight("never-heard-of") == pytest.approx(0.3)
