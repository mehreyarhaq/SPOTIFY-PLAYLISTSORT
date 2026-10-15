"""The steps the CLI composes: assign, publish, query, sync. No typer, no printing."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from playlistsort import db
from playlistsort.config import Settings
from playlistsort.model.assign import (
    Assignment,
    assign_from_probs,
    assignments_for,
    write_assignments,
)
from playlistsort.model.classifier import load_model, predict_library
from playlistsort.model.feel import feel_axes, feel_bucket, order_for_set
from playlistsort.model.query import select_tracks
from playlistsort.model.scorer import load_weights, score_library
from playlistsort.spotify import publish as pub
from playlistsort.taxonomy.normalize import load_taxonomy


class NoAssignmentsError(RuntimeError):
    pass


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")


# --------------------------------------------------------------------------- assign


@dataclass
class AssignResult:
    run_id: str
    axis: str
    source: str  # model | evidence | feel-buckets
    per_track: dict[str, list[Assignment]]
    per_category: dict[str, int] = field(default_factory=dict)

    @property
    def assigned_tracks(self) -> int:
        return sum(1 for a in self.per_track.values() if a)


def assign_run(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    axis: str = "genre",
    run_id: str | None = None,
    threshold: float | None = None,
    use_model: bool = True,
) -> AssignResult:
    tax = load_taxonomy(settings.taxonomy_path)
    weights = load_weights(settings.weights_path)
    model_path = settings.data_dir / "models" / f"{axis}.joblib"
    if use_model and model_path.exists():
        model = load_model(model_path)
        scores = score_library(conn, taxonomy=tax, weights=weights)
        preds = predict_library(conn, model, taxonomy=tax, weights=weights, scores=scores)
        per_track = {
            tid: assign_from_probs(p, weights=weights, taxonomy=tax, threshold=threshold)
            for tid, p in preds.items()
        }
        source = "model"
    elif axis == "feel":
        per_track = {
            tid: [Assignment(feel_bucket(a), 1.0, "primary")] for tid, a in feel_axes(conn).items()
        }
        source = "feel-buckets"
    else:
        scores = score_library(conn, taxonomy=tax, weights=weights)
        per_track = {
            tid: assignments_for(s, weights=weights, threshold=threshold)
            for tid, s in scores.items()
        }
        source = "evidence"
    run = run_id or _stamp()
    write_assignments(conn, run_id=run, axis=axis, per_track=per_track)
    per_category: dict[str, int] = {}
    for assigns in per_track.values():
        for a in assigns:
            per_category[a.category] = per_category.get(a.category, 0) + 1
    return AssignResult(
        run_id=run, axis=axis, source=source, per_track=per_track, per_category=per_category
    )


# --------------------------------------------------------------------------- publish


@dataclass(frozen=True)
class PublishRow:
    name: str
    summary: str
    skipped_local: int
    diff: pub.PlaylistDiff


def _publish_category(
    conn: sqlite3.Connection,
    sp: Any,
    *,
    axis: str,
    category: str,
    name: str,
    uris: list[str],
    user_id: str,
    dry_run: bool,
) -> pub.PlaylistDiff:
    existing = db.get_playlist(conn, axis=axis, category=category)
    spotify_id = existing.spotify_id if existing else None
    current = (
        pub.fetch_current_items(sp, spotify_id)
        if spotify_id and pub.playlist_exists(sp, spotify_id)
        else []
    )
    diff = pub.build_diff(
        axis=axis,
        category=category,
        name=name,
        spotify_id=spotify_id,
        current=current,
        desired=uris,
    )
    pub.apply_diff(sp, conn, diff, user_id=user_id, dry_run=dry_run)
    return diff


def publish_run(
    conn: sqlite3.Connection,
    sp: Any,
    settings: Settings,
    *,
    axis: str = "genre",
    run_id: str | None = None,
    dry_run: bool = False,
) -> list[PublishRow]:
    tax = load_taxonomy(settings.taxonomy_path)
    run = run_id or pub.latest_run_id(conn, axis)
    if run is None:
        raise NoAssignmentsError(
            f"No {axis} assignments yet. Run `playlistsort assign --axis {axis}` first."
        )
    desired = pub.desired_by_category(conn, axis=axis, run_id=run)
    user_id = sp.me()["id"]
    rows: list[PublishRow] = []
    for category, raw_uris in sorted(desired.items()):
        ids = [u.split(":")[-1] if u.startswith("spotify:track:") else u for u in raw_uris]
        uris = [
            t if t.startswith("spotify:") else f"spotify:track:{t}"
            for t in order_for_set(conn, ids)
        ]
        path = tax.path_name(category) if category in tax.tags() else category
        diff = _publish_category(
            conn, sp, axis=axis, category=category, name=pub.playlist_name(path), uris=uris,
            user_id=user_id, dry_run=dry_run,
        )  # fmt: skip
        rows.append(
            PublishRow(
                name=diff.name,
                summary=diff.summary(),
                skipped_local=len(diff.skipped_local),
                diff=diff,
            )
        )
    return rows


def publish_query(
    conn: sqlite3.Connection,
    sp: Any,
    settings: Settings,
    *,
    genre: str | None,
    feel: str | None,
    name: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
) -> pub.PlaylistDiff:
    tax = load_taxonomy(settings.taxonomy_path)
    selection = select_tracks(conn, taxonomy=tax, genre=genre, feel=feel, run_id=run_id)
    title = (name or selection.default_name(tax)).strip()
    uris = [
        tid if tid.startswith("spotify:") else f"spotify:track:{tid}" for tid in selection.track_ids
    ]
    return _publish_category(
        conn, sp, axis="query", category=title.lower(), name=pub.playlist_name(title), uris=uris,
        user_id=sp.me()["id"], dry_run=dry_run,
    )  # fmt: skip


# --------------------------------------------------------------------------- sync


def sync(
    conn: sqlite3.Connection,
    sp: Any,
    settings: Settings,
    *,
    enrich: bool = True,
    publish: bool = True,
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    """Newly liked tracks -> enriched -> assigned -> published. Everything is incremental."""
    from playlistsort.enrich.factory import ORDER, ConfigError, build_enricher
    from playlistsort.spotify import ingest as spotify_ingest

    summary: dict[str, Any] = {}
    stats = spotify_ingest.ingest(sp, conn)
    summary["ingested"] = stats.tracks
    log(f"ingest: {stats.tracks} tracks ({stats.total_reported} reported by Spotify)")
    summary["album_labels"] = spotify_ingest.fetch_album_labels(sp, conn)
    if enrich:
        for name in ORDER:
            try:
                enricher = build_enricher(name, settings, conn)
            except ConfigError as exc:
                log(f"{name}: skipped ({exc})")
                continue
            st = enricher.enrich(conn, only_missing=True)
            summary[name] = st.summary()
            log(st.summary())
    result = assign_run(conn, settings, axis="genre")
    summary["assigned"] = result.assigned_tracks
    log(f"assign ({result.source}): {result.assigned_tracks} tracks, run {result.run_id}")
    if publish:
        rows = publish_run(conn, sp, settings, axis="genre", run_id=result.run_id)
        summary["published"] = {r.name: r.summary for r in rows}
        changed = sum(1 for r in rows if r.diff.changed)
        log(f"publish: {changed} playlists changed, {len(rows) - changed} unchanged")
    return summary
