"""Create and update the managed Spotify playlists, idempotently.

The `playlists` table is the source of truth for which Spotify playlist holds which category.
`build_diff` is pure: given the current and desired track URIs it decides between no change,
an incremental add/remove, or a full replace (new playlist, reorder, or > 50% churn).
`smoke_test` exercises every write path once against a throwaway playlist.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import spotipy
from spotipy.exceptions import SpotifyException

from playlistsort import db

LOCAL_PREFIX = "spotify:local:"
NAME_PREFIX = "[PS] "
DESCRIPTION = "Managed by playlistsort. Edits made here are overwritten on the next publish."
CHUNK = 100


def playlist_name(path: str, prefix: str = NAME_PREFIX) -> str:
    return f"{prefix}{path}"


def clean_desired(uris: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Drop local tracks (cannot be added through the API) and duplicates, keeping order."""
    seen: set[str] = set()
    kept: list[str] = []
    skipped: list[str] = []
    for uri in uris:
        if uri.startswith(LOCAL_PREFIX):
            skipped.append(uri)
            continue
        if uri in seen:
            continue
        seen.add(uri)
        kept.append(uri)
    return tuple(kept), tuple(skipped)


def chunked(seq: Sequence[Any], size: int = CHUNK) -> Iterator[list[Any]]:
    for i in range(0, len(seq), size):
        yield list(seq[i : i + size])


@dataclass(frozen=True)
class PlaylistDiff:
    axis: str
    category: str
    name: str
    spotify_id: str | None
    desired: tuple[str, ...]
    add: tuple[str, ...]
    remove: tuple[str, ...]
    replace: bool
    skipped_local: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.replace or bool(self.add) or bool(self.remove)

    def summary(self) -> str:
        if self.spotify_id is None:
            return f"create with {len(self.desired)} tracks"
        if not self.changed:
            return "unchanged"
        if self.replace:
            return f"replace all ({len(self.desired)} tracks)"
        return f"+{len(self.add)} / -{len(self.remove)}"


def build_diff(
    *,
    axis: str,
    category: str,
    name: str,
    spotify_id: str | None,
    current: Sequence[str],
    desired: Sequence[str],
    replace_threshold: float = 0.5,
) -> PlaylistDiff:
    wanted, skipped = clean_desired(desired)
    have = tuple(current)
    base = dict(axis=axis, category=category, name=name, spotify_id=spotify_id, desired=wanted)
    if spotify_id is None:
        return PlaylistDiff(**base, add=wanted, remove=(), replace=True, skipped_local=skipped)
    if have == wanted:
        return PlaylistDiff(**base, add=(), remove=(), replace=False, skipped_local=skipped)
    have_set, want_set = set(have), set(wanted)
    add = tuple(u for u in wanted if u not in have_set)
    remove = tuple(u for u in have if u not in want_set)
    if not add and not remove:  # same set, different order
        return PlaylistDiff(**base, add=(), remove=(), replace=True, skipped_local=skipped)
    churn = (len(add) + len(remove)) / max(len(wanted), 1)
    return PlaylistDiff(
        **base, add=add, remove=remove, replace=churn > replace_threshold, skipped_local=skipped
    )


# --------------------------------------------------------------------------- Spotify I/O


def _entry_uri(entry: dict[str, Any]) -> str | None:
    item = entry.get("item") or entry.get("track") or {}
    return item.get("uri")


def fetch_current_items(sp: spotipy.Spotify, playlist_id: str) -> list[str]:
    uris: list[str] = []
    offset = 0
    while True:
        page = sp.playlist_items(playlist_id, limit=CHUNK, offset=offset)
        entries = page.get("items") or []
        for entry in entries:
            uri = _entry_uri(entry)
            if uri:
                uris.append(uri)
        if not entries or page.get("next") is None:
            return uris
        offset += len(entries)


def playlist_exists(sp: spotipy.Spotify, playlist_id: str) -> bool:
    try:
        sp.playlist(playlist_id, fields="id")
        return True
    except SpotifyException as exc:
        if exc.http_status == 404:
            return False
        raise


def create_playlist(sp: spotipy.Spotify, user_id: str, name: str) -> str:
    created = sp.user_playlist_create(user_id, name, public=False, description=DESCRIPTION)
    return created["id"]


@dataclass
class ApplyResult:
    diff: PlaylistDiff
    playlist_id: str | None
    created: bool = False
    requests: int = 0
    dry_run: bool = False


def apply_diff(
    sp: spotipy.Spotify,
    conn: sqlite3.Connection,
    diff: PlaylistDiff,
    *,
    user_id: str,
    dry_run: bool = False,
) -> ApplyResult:
    result = ApplyResult(diff=diff, playlist_id=diff.spotify_id, dry_run=dry_run)
    if not diff.changed and diff.spotify_id:
        return result
    if dry_run:
        return result

    pid = diff.spotify_id
    if pid is None or not playlist_exists(sp, pid):
        pid = create_playlist(sp, user_id, diff.name)
        result.created = True
        result.requests += 1
    result.playlist_id = pid

    if diff.replace or result.created:
        first, rest = list(diff.desired[:CHUNK]), diff.desired[CHUNK:]
        sp.playlist_replace_items(pid, first)
        result.requests += 1
        for chunk in chunked(rest):
            sp.playlist_add_items(pid, chunk)
            result.requests += 1
    else:
        for chunk in chunked(diff.remove):
            sp.playlist_remove_all_occurrences_of_items(pid, chunk)
            result.requests += 1
        for chunk in chunked(diff.add):
            sp.playlist_add_items(pid, chunk)
            result.requests += 1

    with conn:
        db.set_playlist(
            conn, axis=diff.axis, category=diff.category, spotify_id=pid, name=diff.name
        )
    return result


# --------------------------------------------------------------------------- smoke test


@dataclass
class SmokeStep:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class SmokeResult:
    steps: list[SmokeStep] = field(default_factory=list)
    playlist_id: str | None = None

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)


def _fail_detail(exc: Exception) -> str:
    if isinstance(exc, SpotifyException):
        return f"HTTP {exc.http_status} {exc.msg}"
    return f"{type(exc).__name__}: {exc}"


def smoke_test(sp: spotipy.Spotify, *, cleanup: bool = True) -> SmokeResult:
    """Exercise read + every playlist write path once. Never touches existing playlists."""
    res = SmokeResult()

    def step(name: str, fn, *, stop_on_fail: bool = True):
        try:
            detail = fn()
            res.steps.append(SmokeStep(name, True, detail or ""))
            return True
        except Exception as exc:  # noqa: BLE001 - we want every failure reported, not raised
            res.steps.append(SmokeStep(name, False, _fail_detail(exc)))
            return not stop_on_fail

    state: dict[str, Any] = {}

    def s_me():
        me = sp.me()
        state["user_id"] = me["id"]
        return f"user={me['id']} product={me.get('product')} country={me.get('country')}"

    def s_saved():
        page = sp.current_user_saved_tracks(limit=2)
        items = [i for i in page.get("items") or [] if i.get("track")]
        state["uris"] = [i["track"]["uri"] for i in items if not i["track"].get("is_local")]
        state["artist_id"] = next(
            (a["id"] for i in items for a in i["track"].get("artists", []) if a.get("id")), None
        )
        isrc = (items[0]["track"].get("external_ids") or {}).get("isrc") if items else None
        return f"total={page.get('total')} first_isrc={isrc or 'MISSING'}"

    def s_create():
        created = sp.user_playlist_create(
            state["user_id"],
            "[PS] smoke test (safe to delete)",
            public=False,
            description=DESCRIPTION,
        )
        state["pid"] = res.playlist_id = created["id"]
        return f"id={created['id']} public={created.get('public')}"

    def s_add():
        uris = state.get("uris") or []
        if len(uris) < 2:
            return "skipped: fewer than 2 saved tracks"
        out = sp.playlist_add_items(state["pid"], uris[:2])
        return f"snapshot={out.get('snapshot_id')}"

    def s_read_back():
        uris = state.get("uris") or []
        got = fetch_current_items(sp, state["pid"])
        if len(uris) >= 2 and got != uris[:2]:
            raise AssertionError(f"expected {uris[:2]}, got {got}")
        return f"{len(got)} items in order"

    def s_replace():
        uris = state.get("uris") or []
        if not uris:
            return "skipped"
        sp.playlist_replace_items(state["pid"], uris[:1])
        got = fetch_current_items(sp, state["pid"])
        if got != uris[:1]:
            raise AssertionError(f"expected {uris[:1]}, got {got}")
        return "replace -> 1 item"

    def s_remove():
        uris = state.get("uris") or []
        if not uris:
            return "skipped"
        sp.playlist_remove_all_occurrences_of_items(state["pid"], uris[:1])
        got = fetch_current_items(sp, state["pid"])
        if got:
            raise AssertionError(f"expected empty, got {got}")
        return "remove -> 0 items"

    def s_artist():
        aid = state.get("artist_id")
        if not aid:
            return "skipped"
        data = sp.artist(aid)
        genres = data.get("genres") or []
        return f"{data.get('name')}: genres={genres or 'EMPTY'}"

    def s_cleanup():
        sp.current_user_unfollow_playlist(state["pid"])
        return "test playlist removed"

    ok = step("me", s_me)
    ok = ok and step("saved tracks + isrc", s_saved)
    ok = ok and step("create private playlist", s_create)
    if ok:
        step("add 2 items", s_add, stop_on_fail=False)
        step("read back in order", s_read_back, stop_on_fail=False)
        step("replace items", s_replace, stop_on_fail=False)
        step("remove items", s_remove, stop_on_fail=False)
        step("artist genres present?", s_artist, stop_on_fail=False)
        if cleanup:
            time.sleep(0.5)
            step("cleanup", s_cleanup, stop_on_fail=False)
    return res


# --------------------------------------------------------------------------- from assignments


def latest_run_id(conn: sqlite3.Connection, axis: str) -> str | None:
    row = conn.execute("select max(run_id) from assignments where axis=?", (axis,)).fetchone()
    return row[0] if row and row[0] else None


def desired_by_category(
    conn: sqlite3.Connection, *, axis: str, run_id: str
) -> dict[str, list[str]]:
    """Category -> ordered track URIs (highest probability first) for one assignment run."""
    rows = conn.execute(
        """
        select a.category, a.track_id, t.is_local
        from assignments a join tracks t on t.spotify_id = a.track_id
        where a.axis=? and a.run_id=?
        order by a.category, a.prob desc, t.added_at desc
        """,
        (axis, run_id),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        uri = r["track_id"] if r["is_local"] else f"spotify:track:{r['track_id']}"
        out.setdefault(r["category"], []).append(uri)
    return out
