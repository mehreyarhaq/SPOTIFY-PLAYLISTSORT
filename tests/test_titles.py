"""Table-driven tests for the title / mix-name parser."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from playlistsort.titles import ParsedTitle, parse_title

FIXTURE = Path(__file__).parent / "fixtures" / "titles.csv"


def _rows() -> list[dict[str, str]]:
    with FIXTURE.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _split(value: str) -> tuple[str, ...]:
    return tuple(v for v in value.split("|") if v)


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r["title"])
def test_parse_title_table(row: dict[str, str]) -> None:
    parsed = parse_title(row["title"], _split(row["artists"]))
    assert isinstance(parsed, ParsedTitle)
    expected = {
        "base_title": row["base_title"],
        "mix_name": row["mix_name"] or None,
        "mix_type": row["mix_type"] or None,
        "remixers": _split(row["remixers"]),
        "featuring": _split(row["featuring"]),
        "is_remix": row["is_remix"] == "true",
    }
    actual = {
        "base_title": parsed.base_title,
        "mix_name": parsed.mix_name,
        "mix_type": parsed.mix_type,
        "remixers": parsed.remixers,
        "featuring": parsed.featuring,
        "is_remix": parsed.is_remix,
    }
    assert actual == expected


def test_fixture_has_enough_coverage() -> None:
    assert len(_rows()) >= 40


def test_confidence_is_high_for_clean_remix() -> None:
    parsed = parse_title("Cola - Franky Rizardo Remix", ("CamelPhat",))
    assert parsed.confidence >= 0.9


def test_confidence_drops_when_remixer_is_unnamed() -> None:
    parsed = parse_title("Can You Feel It - Remix", ("Fingers Inc.",))
    assert parsed.is_remix
    assert parsed.confidence < 0.9


def test_confidence_drops_for_unrecognised_parenthetical() -> None:
    parsed = parse_title("Hot Creations Anthem (Hot Creations)", ("Jamie Jones",))
    assert parsed.mix_name is None
    assert parsed.confidence < 0.9


def test_plain_title_is_fully_confident() -> None:
    parsed = parse_title("Live Your Life", ("Yuna",))
    assert parsed.confidence == 1.0
    assert parsed.mix_type is None
