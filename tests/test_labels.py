"""Review CSV export and user label import."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from playlistsort import db
from playlistsort.labels import export_review_csv, import_labels_csv, resolve_category
from playlistsort.model.scorer import load_weights, score_library
from playlistsort.taxonomy.normalize import load_taxonomy

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tax():
    return load_taxonomy(ROOT / "config" / "taxonomy.yaml")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    with c:
        db.upsert_track(c, spotify_id="t1", title="Cola", base_title="Cola", added_at="2026-01-01")
        db.upsert_track(c, spotify_id="t2", title="Rej", base_title="Rej", added_at="2026-01-02")
        db.upsert_track(
            c, spotify_id="t3", title="Mystery", base_title="Mystery", added_at="2026-01-03"
        )
        db.upsert_artist(c, spotify_id="a1", name="CamelPhat")
        db.link_track_artist(c, track_id="t1", artist_id="a1")
        db.add_evidence(c, db.Evidence("t1", "beatport", "genre", "Tech House", source_tag_id="11"))
        db.add_evidence(c, db.Evidence("t2", "beatport", "genre", "Tech House", source_tag_id="11"))
        db.add_evidence(c, db.Evidence("t2", "sonovault", "genre", "Deep House", match_method="id"))
    return c


def test_resolve_category_accepts_keys_names_paths_and_aliases(tax) -> None:
    assert resolve_category("tech_house", tax) == "tech_house"
    assert resolve_category("Tech House", tax) == "tech_house"
    assert resolve_category("Minimal / Deep Tech › Deep Tech", tax) == "minimal_deep_tech.deep_tech"
    assert resolve_category("minimal / deep tech > deep tech", tax) == "minimal_deep_tech.deep_tech"
    assert resolve_category("melodic techno", tax) == "melodic_house_techno.melodic_techno"
    assert resolve_category("totally made up", tax) is None


def test_export_review_csv_lists_tracks_with_scores(conn, tax, tmp_path) -> None:
    weights = load_weights(ROOT / "config" / "weights.yaml")
    scores = score_library(conn, taxonomy=tax, weights=weights)
    out = tmp_path / "review.csv"
    n = export_review_csv(conn, out, scores=scores, taxonomy=tax)
    assert n == 3
    rows = {r["track_id"]: r for r in csv.DictReader(out.open(encoding="utf-8"))}
    assert rows["t1"]["artists"] == "CamelPhat"
    assert rows["t1"]["status"] == "ok" and rows["t1"]["top1"] == "Tech House"
    assert rows["t2"]["status"] == "between" and rows["t2"]["top2"] == "Deep House"
    assert rows["t3"]["status"] == "unknown" and rows["t3"]["top1"] == ""
    assert "beatport: Tech House" in rows["t1"]["evidence"]
    assert rows["t1"]["spotify_url"] == "https://open.spotify.com/track/t1"
    assert rows["t1"]["your_genre"] == "" and "also" in rows["t1"] and "note" in rows["t1"]


def test_export_review_only_keeps_uncertain_tracks(conn, tax, tmp_path) -> None:
    weights = load_weights(ROOT / "config" / "weights.yaml")
    scores = score_library(conn, taxonomy=tax, weights=weights)
    out = tmp_path / "review.csv"
    export_review_csv(conn, out, scores=scores, taxonomy=tax, only="review")
    ids = [r["track_id"] for r in csv.DictReader(out.open(encoding="utf-8"))]
    assert ids == ["t2", "t3"]  # between first, then unknown; the confident t1 is left out


def test_import_labels_writes_sessions_between_and_negatives(conn, tax, tmp_path) -> None:
    src = tmp_path / "labels.csv"
    with src.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["track_id", "your_genre", "also", "note"])
        w.writeheader()
        w.writerow({"track_id": "t1", "your_genre": "Tech House", "also": "", "note": "classic"})
        w.writerow({"track_id": "t2", "your_genre": "Deep House", "also": "Tech House", "note": ""})
        w.writerow({"track_id": "t3", "your_genre": "not Afro House", "also": "", "note": ""})
        w.writerow({"track_id": "t3", "your_genre": "", "also": "", "note": "left blank"})
        w.writerow({"track_id": "t3", "your_genre": "no such genre", "also": "", "note": ""})
    result = import_labels_csv(conn, src, taxonomy=tax, session_id="s1")
    assert result.labels == 4
    assert result.skipped == 1
    assert len(result.errors) == 1 and "no such genre" in result.errors[0]
    rows = conn.execute(
        "select track_id, category, weight, kind, note from labels order by id"
    ).fetchall()
    got = [(r["track_id"], r["category"], r["weight"], r["kind"]) for r in rows]
    assert got == [
        ("t1", "tech_house", 1.0, "seed"),
        ("t2", "deep_house", 0.5, "between"),
        ("t2", "tech_house", 0.5, "between"),
        ("t3", "afro_house", 1.0, "negative"),
    ]
    assert rows[0]["note"] == "classic"
    latest = db.latest_labels(conn, axis="genre")
    assert latest["t2"] == [("deep_house", 0.5, "between"), ("tech_house", 0.5, "between")]
