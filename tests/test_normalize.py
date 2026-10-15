"""Raw source tags -> canonical taxonomy tags."""

from __future__ import annotations

from pathlib import Path

import pytest

from playlistsort.taxonomy.normalize import Taxonomy, load_taxonomy, normalize

CONFIG = Path(__file__).resolve().parents[1] / "config" / "taxonomy.yaml"


@pytest.fixture(scope="module")
def tax() -> Taxonomy:
    return load_taxonomy(CONFIG)


def _tags(result) -> list[str]:
    return [c.tag for c in result]


def test_loads_genres_and_subgenres(tax: Taxonomy) -> None:
    assert "tech_house" in tax.genres
    assert "minimal_deep_tech.deep_tech" in tax.tags()
    assert tax.display_name("minimal_deep_tech.deep_tech") == "Deep Tech"
    assert tax.path_name("minimal_deep_tech.deep_tech") == "Minimal / Deep Tech › Deep Tech"
    assert tax.path_name("tech_house") == "Tech House"


def test_exact_genre_name(tax: Taxonomy) -> None:
    (c,) = normalize("Tech House", "beatport", tax)
    assert c.tag == "tech_house"
    assert c.specificity == "genre"
    assert c.family == "house"
    assert c.weight == 1.0


def test_exact_subgenre_name(tax: Taxonomy) -> None:
    (c,) = normalize("Deep Tech", "beatport", tax)
    assert c.tag == "minimal_deep_tech.deep_tech"
    assert c.specificity == "subgenre"
    assert c.genre_key == "minimal_deep_tech"


def test_genre_pipe_subgenre_form(tax: Taxonomy) -> None:
    (c,) = normalize("Afro House | Afro Melodic", "beatport", tax)
    assert c.tag == "afro_house.afro_melodic"


def test_case_and_punctuation_insensitive(tax: Taxonomy) -> None:
    assert _tags(normalize("TECH HOUSE", "lastfm", tax)) == ["tech_house"]
    assert _tags(normalize("tech-house", "lastfm", tax)) == ["tech_house"]
    assert _tags(normalize("Minimal/Deep Tech", "sonovault", tax)) == ["minimal_deep_tech"]
    assert _tags(normalize("  deep  tech ", "musicbrainz", tax)) == ["minimal_deep_tech.deep_tech"]


def test_broad_tag_votes_family_only(tax: Taxonomy) -> None:
    (c,) = normalize("electronic", "spotify", tax)
    assert c.specificity == "family"
    assert c.family == "electronic"
    assert c.genre_key is None


def test_house_is_broad_from_discogs_but_a_genre_from_beatport(tax: Taxonomy) -> None:
    (discogs,) = normalize("House", "discogs", tax)
    assert discogs.specificity == "family"
    assert discogs.family == "house"
    (beatport,) = normalize("House", "beatport", tax)
    assert beatport.tag == "house"
    assert beatport.specificity == "genre"


def test_split_alias_shares_weight(tax: Taxonomy) -> None:
    result = normalize("Afro Tech", "lastfm", tax)
    assert sorted(_tags(result)) == ["afro_house.afro_melodic", "tech_house.latin_tech"]
    assert all(c.weight == pytest.approx(0.5) for c in result)
    assert all(c.via_alias == "afro tech" for c in result)


def test_unknown_tag_returns_empty_and_is_recorded(tax: Taxonomy) -> None:
    assert normalize("zouk bass", "discogs", tax) == ()
    assert tax.unmapped[("discogs", "zouk bass")] >= 1


def test_beatport_id_beats_name(tax: Taxonomy) -> None:
    (c,) = normalize("Some Renamed Genre", "beatport", tax, source_tag_id="11")
    assert c.tag == "tech_house"
    (s,) = normalize("whatever", "beatport", tax, source_tag_id="188")
    assert s.tag == "minimal_deep_tech.deep_tech"


def test_empty_input(tax: Taxonomy) -> None:
    assert normalize("", "beatport", tax) == ()
    assert normalize("   ", "beatport", tax) == ()
