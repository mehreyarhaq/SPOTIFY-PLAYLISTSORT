"""`query`: genre and/or feel -> an ordered list of tracks ready to publish."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from playlistsort.labels import resolve_category
from playlistsort.model.feel import FEEL_BUCKETS, feel_axes, feel_bucket, order_for_set
from playlistsort.spotify.publish import desired_by_category, latest_run_id
from playlistsort.taxonomy.normalize import Taxonomy


class QueryError(ValueError):
    pass


@dataclass(frozen=True)
class Selection:
    track_ids: list[str]
    category: str | None
    feel: str | None
    run_id: str | None

    def default_name(self, taxonomy: Taxonomy) -> str:
        parts = []
        if self.category:
            parts.append(taxonomy.path_name(self.category))
        if self.feel:
            parts.append(self.feel)
        return " · ".join(parts)


def select_tracks(
    conn: sqlite3.Connection,
    *,
    taxonomy: Taxonomy,
    genre: str | None = None,
    feel: str | None = None,
    run_id: str | None = None,
) -> Selection:
    if not genre and not feel:
        raise QueryError("Give --genre, --feel or both.")
    category = None
    ids: set[str] | None = None
    run = run_id or latest_run_id(conn, "genre")
    if genre:
        category = resolve_category(genre, taxonomy)
        if category is None:
            raise QueryError(f"Unknown genre {genre!r}. Try `playlistsort taxonomy show`.")
        if run is None:
            raise QueryError("No genre assignments yet. Run `playlistsort assign` first.")
        uris = desired_by_category(conn, axis="genre", run_id=run).get(category, [])
        ids = {u.split(":")[-1] if u.startswith("spotify:track:") else u for u in uris}
    if feel:
        wanted = feel.strip().lower()
        if wanted not in FEEL_BUCKETS:
            raise QueryError(f"Unknown feel {feel!r}. Choose from: {', '.join(FEEL_BUCKETS)}.")
        axes = feel_axes(conn)
        matching = {tid for tid, a in axes.items() if feel_bucket(a) == wanted}
        ids = matching if ids is None else ids & matching
    ordered = order_for_set(conn, sorted(ids or set()))
    return Selection(
        track_ids=ordered,
        category=category,
        feel=feel.strip().lower() if feel else None,
        run_id=run,
    )
