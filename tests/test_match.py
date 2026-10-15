"""Asymmetric fuzzy matching of our track against a source's candidates."""

from __future__ import annotations

from playlistsort.enrich.match import Candidate, Query, pick_best, score_candidate

Q = Query(
    base_title="Cola",
    mix_name="Franky Rizardo Remix",
    remixers=("Franky Rizardo",),
    artists=("CamelPhat", "Elderbrook"),
    duration_ms=412_000,
    is_remix=True,
)


def cand(**kw) -> Candidate:
    base = dict(
        id="1",
        title="Cola",
        mix_name="Franky Rizardo Remix",
        artists=("CamelPhat", "Elderbrook"),
        remixers=("Franky Rizardo",),
        duration_ms=412_000,
    )
    base.update(kw)
    return Candidate(**base)


def test_exact_candidate_scores_high() -> None:
    s = score_candidate(Q, cand())
    assert s.score >= 0.95


def test_original_mix_is_rejected_for_a_remix_query() -> None:
    s = score_candidate(Q, cand(mix_name="Original Mix", remixers=()))
    assert s.score < 0.5


def test_remix_by_someone_else_is_rejected() -> None:
    s = score_candidate(Q, cand(mix_name="Solardo Remix", remixers=("Solardo",)))
    assert s.score < 0.5


def test_remix_candidate_is_rejected_for_an_original_query() -> None:
    original = Query(
        base_title="Cola",
        mix_name=None,
        remixers=(),
        artists=("CamelPhat", "Elderbrook"),
        duration_ms=380_000,
        is_remix=False,
    )
    s = score_candidate(original, cand())
    assert s.score < 0.5


def test_duration_far_off_is_penalised() -> None:
    close = score_candidate(Q, cand(duration_ms=413_500)).score
    far = score_candidate(Q, cand(duration_ms=300_000)).score
    assert close > far


def test_title_typos_still_match() -> None:
    s = score_candidate(Q, cand(title="Cola (Franky Rizardo Remix)", mix_name=None))
    assert s.score >= 0.72


def test_pick_best_returns_none_below_threshold() -> None:
    assert pick_best(Q, [cand(title="Totally Different Song", artists=("Nobody",))]) is None


def test_pick_best_flags_ambiguous_when_top_two_are_close() -> None:
    best = pick_best(Q, [cand(id="a"), cand(id="b", duration_ms=412_200)])
    assert best is not None
    assert best.method == "fuzzy_ambiguous"


def test_pick_best_is_clear_when_runner_up_is_far() -> None:
    best = pick_best(Q, [cand(id="a"), cand(id="b", title="Other", artists=("X",))])
    assert best is not None
    assert best.candidate.id == "a"
    assert best.method == "fuzzy"
