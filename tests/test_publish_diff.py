"""Pure planning logic for idempotent playlist publishing (no network)."""

from __future__ import annotations

from playlistsort.spotify.publish import (
    PlaylistDiff,
    build_diff,
    chunked,
    clean_desired,
    playlist_name,
)

U = [f"spotify:track:{i:022d}" for i in range(100)]


def test_identical_lists_produce_no_change() -> None:
    d = build_diff(
        axis="genre",
        category="tech_house",
        name="x",
        spotify_id="pl",
        current=U[:50],
        desired=U[:50],
    )
    assert isinstance(d, PlaylistDiff)
    assert d.add == () and d.remove == () and d.replace is False
    assert d.changed is False


def test_new_playlist_is_a_full_replace() -> None:
    d = build_diff(
        axis="genre", category="tech_house", name="x", spotify_id=None, current=[], desired=U[:5]
    )
    assert d.replace is True
    assert d.desired == tuple(U[:5])
    assert d.changed is True


def test_small_change_is_incremental() -> None:
    current = U[:50]
    desired = U[1:51]  # drop one, add one
    d = build_diff(
        axis="genre", category="c", name="x", spotify_id="pl", current=current, desired=desired
    )
    assert d.replace is False
    assert d.add == (U[50],)
    assert d.remove == (U[0],)
    assert d.changed is True


def test_large_change_becomes_a_replace() -> None:
    current = U[:50]
    desired = U[30:80]  # 30 removed, 30 added: 60 operations for a 50-track playlist
    d = build_diff(
        axis="genre", category="c", name="x", spotify_id="pl", current=current, desired=desired
    )
    assert d.replace is True
    assert d.desired == tuple(desired)


def test_reorder_of_same_set_is_a_replace() -> None:
    current = U[:10]
    desired = list(reversed(U[:10]))
    d = build_diff(
        axis="genre", category="c", name="x", spotify_id="pl", current=current, desired=desired
    )
    assert d.replace is True
    assert d.changed is True


def test_local_tracks_are_skipped_and_reported() -> None:
    desired = [U[0], "spotify:local:Artist:Album:Title:200", U[1]]
    cleaned, skipped = clean_desired(desired)
    assert cleaned == (U[0], U[1])
    assert skipped == ("spotify:local:Artist:Album:Title:200",)


def test_duplicates_in_desired_are_dropped_keeping_first() -> None:
    cleaned, _ = clean_desired([U[0], U[1], U[0]])
    assert cleaned == (U[0], U[1])


def test_chunked_splits_at_one_hundred() -> None:
    parts = list(chunked(list(range(250)), 100))
    assert [len(p) for p in parts] == [100, 100, 50]
    assert list(chunked([], 100)) == []


def test_playlist_name_uses_prefix_and_path() -> None:
    assert (
        playlist_name("Minimal / Deep Tech › Deep Tech") == "[PS] Minimal / Deep Tech › Deep Tech"
    )
    assert playlist_name("Tech House", prefix="DJ ") == "DJ Tech House"
