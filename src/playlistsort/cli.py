"""Command-line interface."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from playlistsort import __version__, db
from playlistsort.config import Settings, load_settings
from playlistsort.enrich import beatport as bp_mod
from playlistsort.enrich.factory import ORDER, ConfigError, build_enricher
from playlistsort.labels import export_review_csv, import_labels_csv
from playlistsort.model.assign import assignments_for, write_assignments
from playlistsort.model.scorer import load_weights, score_library
from playlistsort.spotify import auth as spotify_auth
from playlistsort.spotify import ingest as spotify_ingest
from playlistsort.spotify import publish as spotify_publish
from playlistsort.taxonomy.normalize import load_taxonomy, normalize

app = typer.Typer(
    no_args_is_help=True,
    help="Sort a Spotify Liked Songs library into DJ-grade genre (and later feel) playlists.",
)
auth_app = typer.Typer(no_args_is_help=True, help="Spotify login.")
taxonomy_app = typer.Typer(no_args_is_help=True, help="Inspect the genre taxonomy.")
labels_app = typer.Typer(no_args_is_help=True, help="Review CSV export and label import.")
app.add_typer(auth_app, name="auth")
app.add_typer(taxonomy_app, name="taxonomy")
app.add_typer(labels_app, name="labels")

console = Console()
err = Console(stderr=True, style="bold red")


def _settings() -> Settings:
    return load_settings()


def _db(settings: Settings) -> sqlite3.Connection:
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    return conn


def _client(settings: Settings):
    try:
        return spotify_auth.client(settings)
    except spotify_auth.AuthError as exc:
        err.print(str(exc))
        raise typer.Exit(code=2) from exc


@app.callback()
def _main(
    version: Annotated[bool, typer.Option("--version", help="Show version and exit.")] = False,
) -> None:
    if version:
        console.print(f"playlistsort {__version__}")
        raise typer.Exit()


# ------------------------------------------------------------------------------ auth


@auth_app.command("login")
def auth_login(
    paste_redirect: Annotated[
        bool,
        typer.Option(
            "--paste-redirect",
            help="Headless mode: print the login URL and paste the redirect URL back.",
        ),
    ] = False,
) -> None:
    """Log in to Spotify (PKCE). Stores the token under data/tokens/."""
    settings = _settings()
    try:
        spotify_auth.login(
            settings,
            paste_redirect=paste_redirect,
            prompt=lambda p: typer.prompt(p),
            echo=console.print,
        )
        sp = spotify_auth.client(settings)
        who = spotify_auth.whoami(sp)
    except spotify_auth.AuthError as exc:
        err.print(str(exc))
        raise typer.Exit(code=2) from exc
    console.print(f"Logged in as [bold]{who['display_name'] or who['id']}[/] ({who['product']}).")
    console.print(f"Token stored at {settings.token_path}")


@auth_app.command("status")
def auth_status() -> None:
    """Show who is logged in."""
    settings = _settings()
    sp = _client(settings)
    who = spotify_auth.whoami(sp)
    console.print(who)


# ------------------------------------------------------------------------------ ingest


@app.command()
def ingest(
    limit: Annotated[int | None, typer.Option(help="Stop after N tracks (for testing).")] = None,
    album_labels: Annotated[
        bool, typer.Option(help="Also fetch record labels (one request per album).")
    ] = True,
    artist_genres: Annotated[
        bool,
        typer.Option(help="Also fetch Spotify artist genres (sparse; one request per artist)."),
    ] = False,
) -> None:
    """Read Liked Songs into data/library.sqlite."""
    settings = _settings()
    sp = _client(settings)
    conn = _db(settings)

    def progress(done: int, total: int | None) -> None:
        if done % 100 == 0 or (total and done == total):
            console.print(f"  {done}/{total or '?'} tracks")

    stats = spotify_ingest.ingest(sp, conn, limit=limit, progress=progress)
    console.print(
        f"Ingested [bold]{stats.tracks}[/] tracks ({stats.local} local), "
        f"{stats.artists} artists, {stats.albums} albums. Spotify reports {stats.total_reported}."
    )
    if album_labels:
        n = spotify_ingest.fetch_album_labels(sp, conn, limit=limit)
        console.print(f"Fetched labels for {n} albums.")
    if artist_genres:
        n = spotify_ingest.fetch_artist_genres(sp, conn, limit=limit)
        console.print(f"Fetched genres for {n} artists.")


# ------------------------------------------------------------------------------ taxonomy


@taxonomy_app.command("show")
def taxonomy_show() -> None:
    """Print the canonical genre tree."""
    tax = load_taxonomy(_settings().taxonomy_path)
    for g in tax.genres.values():
        console.print(f"[bold]{g.name}[/]  ({g.key}, family={g.family})")
        for s in g.subgenres.values():
            console.print(f"    › {s.name}  ({g.key}.{s.key})")
    console.print(
        f"\n{len(tax.genres)} genres, {len(tax.tags()) - len(tax.genres)} sub-genres, "
        f"{len(tax.aliases)} aliases (taxonomy v{tax.version})"
    )


@taxonomy_app.command("unmapped")
def taxonomy_unmapped() -> None:
    """List raw evidence tags that no alias maps yet."""
    settings = _settings()
    conn = _db(settings)
    tax = load_taxonomy(settings.taxonomy_path)
    rows = conn.execute(
        "select source, raw_value, source_tag_id, count(*) as n from evidence"
        " group by source, raw_value, source_tag_id order by n desc"
    ).fetchall()
    table = Table("source", "raw tag", "tracks")
    missing = 0
    for r in rows:
        if not normalize(r["raw_value"], r["source"], tax, source_tag_id=r["source_tag_id"]):
            table.add_row(r["source"], r["raw_value"], str(r["n"]))
            missing += 1
    if missing:
        console.print(table)
        console.print(f"{missing} unmapped tags. Add aliases to {settings.taxonomy_path}.")
    else:
        console.print("Every evidence tag maps to the taxonomy." if rows else "No evidence yet.")


# ------------------------------------------------------------------------------ enrich


def _coverage_table(conn: sqlite3.Connection) -> Table | str:
    total = conn.execute("select count(*) from tracks where is_local=0").fetchone()[0] or 1
    rows = conn.execute(
        "select source, count(distinct track_id) as n from evidence group by source order by n desc"
    ).fetchall()
    if not rows:
        return "No evidence yet. Run: playlistsort enrich"
    cov = Table("source", "tracks with evidence", "coverage")
    for r in rows:
        cov.add_row(r["source"], str(r["n"]), f"{100 * r['n'] / total:.1f}%")
    genre_cov = conn.execute(
        "select count(distinct track_id) from evidence where field in ('genre','subgenre','style')"
    ).fetchone()[0]
    cov.add_row("[bold]any genre tag[/]", str(genre_cov), f"{100 * genre_cov / total:.1f}%")
    return cov


@app.command()
def enrich(
    source: Annotated[
        str, typer.Option(help="sonovault, beatport, discogs, deezer or all (runs in that order).")
    ] = "all",
    limit: Annotated[int | None, typer.Option(help="Only process N tracks per source.")] = None,
    all_tracks: Annotated[
        bool,
        typer.Option(
            "--all-tracks", help="Re-run for tracks that already have evidence from the source."
        ),
    ] = False,
) -> None:
    """Look every track up in the metadata sources and store the raw tags as evidence."""
    settings = _settings()
    conn = _db(settings)
    names = list(ORDER) if source == "all" else [source]
    for name in names:
        try:
            enricher = build_enricher(name, settings, conn)
        except ConfigError as exc:
            err.print(f"{name}: skipped. {exc}")
            continue
        console.print(f"[bold]{name}[/] ...")

        def progress(done: int, total: int) -> None:
            if done % 50 == 0 or done == total:
                console.print(f"  {done}/{total}")

        stats = enricher.enrich(conn, only_missing=not all_tracks, limit=limit, progress=progress)
        console.print("  " + stats.summary())
    console.print(_coverage_table(conn))


@taxonomy_app.command("pull")
def taxonomy_pull() -> None:
    """Fetch Beatport's current genre tree, store a snapshot, and report what the config lacks."""
    settings = _settings()
    conn = _db(settings)
    tax = load_taxonomy(settings.taxonomy_path)
    try:
        enricher = build_enricher("beatport", settings, conn)
    except ConfigError as exc:
        err.print(str(exc))
        raise typer.Exit(code=2) from exc
    genres = bp_mod.pull_taxonomy(enricher, conn)  # type: ignore[arg-type]
    diff = bp_mod.taxonomy_diff(genres, tax)
    subs = sum(len(g.get("sub_genres") or []) for g in genres)
    console.print(f"Beatport reports {len(genres)} genres and {subs} sub-genres.")
    if diff["missing_genres"] or diff["missing_subgenres"]:
        console.print("[yellow]Not in config/taxonomy.yaml yet:[/]")
        for name in diff["missing_genres"]:
            console.print(f"  genre: {name}")
        for name in diff["missing_subgenres"]:
            console.print(f"  sub-genre: {name}")
    else:
        console.print("[green]config/taxonomy.yaml covers everything Beatport currently lists.[/]")


# ------------------------------------------------------------------------------ score / assign


def _scored(settings: Settings, conn: sqlite3.Connection):
    tax = load_taxonomy(settings.taxonomy_path)
    weights = load_weights(settings.weights_path)
    return tax, weights, score_library(conn, taxonomy=tax, weights=weights)


@app.command()
def score(
    track: Annotated[
        str | None,
        typer.Option(help="Spotify track id: show the evidence breakdown for one track."),
    ] = None,
    top: Annotated[int, typer.Option(help="How many genres to list in the overview.")] = 20,
) -> None:
    """Fuse the collected evidence into genre probabilities and summarise the library."""
    settings = _settings()
    conn = _db(settings)
    tax, weights, scores = _scored(settings, conn)
    if track:
        s = scores.get(track)
        if s is None:
            err.print(f"Unknown track id {track!r} (local files are not scored).")
            raise typer.Exit(code=1)
        row = conn.execute("select title from tracks where spotify_id=?", (track,)).fetchone()
        console.print(f"[bold]{row['title'] if row else track}[/]  status={s.status}")
        contrib = Table("source", "raw tag", "canonical", "weight")
        for src, raw, tag, w in sorted(s.contributions, key=lambda c: -c[3]):
            contrib.add_row(
                src, raw, tax.path_name(tag) if not tag.startswith("family:") else tag, f"{w:.3f}"
            )
        console.print(contrib)
        ranked = Table("genre", "probability")
        for tag, prob in s.ranked[:top]:
            ranked.add_row(tax.path_name(tag), f"{prob:.2f}")
        console.print(ranked)
        return

    counts = {"ok": 0, "between": 0, "unknown": 0}
    leaders: dict[str, int] = {}
    for s in scores.values():
        counts[s.status] = counts.get(s.status, 0) + 1
        if s.top:
            leaders[s.top[0]] = leaders.get(s.top[0], 0) + 1
    total = len(scores) or 1
    overview = Table("status", "tracks", "share")
    for k in ("ok", "between", "unknown"):
        overview.add_row(k, str(counts[k]), f"{100 * counts[k] / total:.1f}%")
    console.print(overview)
    dist = Table("leading genre", "tracks")
    for tag, n in sorted(leaders.items(), key=lambda kv: -kv[1])[:top]:
        dist.add_row(tax.path_name(tag), str(n))
    console.print(dist)


@app.command()
def assign(
    axis: Annotated[str, typer.Option(help="genre (feel arrives in Milestone 6).")] = "genre",
    run_id: Annotated[
        str | None, typer.Option(help="Name for this assignment run (default: timestamp).")
    ] = None,
    threshold: Annotated[
        float | None, typer.Option(help="Override assignment.threshold from config/weights.yaml.")
    ] = None,
) -> None:
    """Decide which playlist(s) each track belongs in and store that as an assignment run."""
    from datetime import UTC, datetime

    settings = _settings()
    conn = _db(settings)
    tax, weights, scores = _scored(settings, conn)
    per_track = {
        tid: assignments_for(s, weights=weights, threshold=threshold) for tid, s in scores.items()
    }
    run = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    rows = write_assignments(conn, run_id=run, axis=axis, per_track=per_track)
    assigned = sum(1 for a in per_track.values() if a)
    console.print(
        f"Run [bold]{run}[/]: {assigned}/{len(per_track)} tracks assigned, "
        f"{rows} playlist memberships."
    )
    per_cat: dict[str, int] = {}
    for assigns in per_track.values():
        for a in assigns:
            per_cat[a.category] = per_cat.get(a.category, 0) + 1
    table = Table("playlist", "tracks")
    for cat, n in sorted(per_cat.items(), key=lambda kv: -kv[1]):
        table.add_row(tax.path_name(cat), str(n))
    console.print(table)
    console.print("Next: `playlistsort publish --dry-run` then `playlistsort publish`.")


@app.command()
def review(
    limit: Annotated[int, typer.Option(help="How many uncertain tracks to list.")] = 25,
) -> None:
    """Show the tracks the evidence cannot place confidently."""
    settings = _settings()
    conn = _db(settings)
    tax, _weights, scores = _scored(settings, conn)
    uncertain = [s for s in scores.values() if s.status != "ok"]
    uncertain.sort(key=lambda s: (0 if s.status == "between" else 1, s.margin))
    titles = {
        r["spotify_id"]: r["title"] for r in conn.execute("select spotify_id, title from tracks")
    }
    table = Table("status", "track", "top", "runner-up", "margin")
    for s in uncertain[:limit]:
        table.add_row(
            s.status,
            titles.get(s.track_id, s.track_id)[:60],
            tax.path_name(s.top[0])
            if s.top
            else (f"family: {s.family_hint}" if s.family_hint else ""),
            tax.path_name(s.second[0]) if s.second else "",
            f"{s.margin:.2f}" if s.top else "",
        )
    console.print(table)
    console.print(
        f"{sum(1 for s in uncertain if s.status == 'between')} between, "
        f"{sum(1 for s in uncertain if s.status == 'unknown')} unknown, "
        f"out of {len(scores)} tracks. "
        "Export them with `playlistsort labels export review.csv --only review`."
    )


@labels_app.command("export")
def labels_export(
    path: Annotated[Path, typer.Argument(help="CSV file to write.")],
    only: Annotated[str, typer.Option(help="all or review (between + unknown only).")] = "all",
) -> None:
    """Write the review spreadsheet: scores, evidence and empty columns for your labels."""
    settings = _settings()
    conn = _db(settings)
    tax, _weights, scores = _scored(settings, conn)
    n = export_review_csv(conn, path, scores=scores, taxonomy=tax, only=only)
    console.print(
        f"Wrote {n} rows to {path}. Fill in `your_genre` (and optionally `also`, `note`)."
    )


@labels_app.command("import")
def labels_import(
    path: Annotated[Path, typer.Argument(help="CSV file with your_genre filled in.")],
    session_id: Annotated[
        str | None, typer.Option(help="Label session name (default: timestamp).")
    ] = None,
) -> None:
    """Read your labels back. Later sessions override earlier ones per track."""
    settings = _settings()
    conn = _db(settings)
    tax = load_taxonomy(settings.taxonomy_path)
    result = import_labels_csv(conn, path, taxonomy=tax, session_id=session_id)
    console.print(
        f"Session {result.session_id}: {result.labels} labels stored, {result.skipped} rows blank."
    )
    for e in result.errors:
        err.print(e)
    if result.errors:
        raise typer.Exit(code=1)


# ------------------------------------------------------------------------------ publish


@app.command()
def publish(
    axis: Annotated[str, typer.Option(help="genre or feel")] = "genre",
    run_id: Annotated[
        str | None, typer.Option(help="Assignment run to publish (default latest).")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the diff, change nothing.")
    ] = False,
    smoke_test: Annotated[
        bool, typer.Option("--smoke-test", help="Create/fill/replace/delete a throwaway playlist.")
    ] = False,
    keep_smoke_playlist: Annotated[
        bool, typer.Option(help="Do not delete the smoke-test playlist.")
    ] = False,
) -> None:
    """Create or update the managed playlists from the latest assignments."""
    settings = _settings()
    sp = _client(settings)

    if smoke_test:
        result = spotify_publish.smoke_test(sp, cleanup=not keep_smoke_playlist)
        table = Table("step", "ok", "detail")
        for s in result.steps:
            table.add_row(s.name, "[green]yes[/]" if s.ok else "[red]NO[/]", s.detail)
        console.print(table)
        if not result.ok:
            err.print("Smoke test failed. Fix the failing step before ingesting or publishing.")
            raise typer.Exit(code=1)
        console.print("[green]Smoke test passed.[/] Playlist write access works.")
        return

    conn = _db(settings)
    tax = load_taxonomy(settings.taxonomy_path)
    run = run_id or spotify_publish.latest_run_id(conn, axis)
    if run is None:
        console.print(
            "No assignments yet. Milestone 3 adds `score` and `assign`; nothing to publish."
        )
        raise typer.Exit(code=0)
    desired = spotify_publish.desired_by_category(conn, axis=axis, run_id=run)
    user_id = spotify_auth.whoami(sp)["id"]
    table = Table("playlist", "change", "skipped local")
    for category, uris in sorted(desired.items()):
        existing = db.get_playlist(conn, axis=axis, category=category)
        name = spotify_publish.playlist_name(
            tax.path_name(category) if category in tax.tags() else category
        )
        current = (
            spotify_publish.fetch_current_items(sp, existing.spotify_id)
            if existing and spotify_publish.playlist_exists(sp, existing.spotify_id)
            else []
        )
        diff = spotify_publish.build_diff(
            axis=axis,
            category=category,
            name=name,
            spotify_id=existing.spotify_id if existing and current is not None else None,
            current=current,
            desired=uris,
        )
        spotify_publish.apply_diff(sp, conn, diff, user_id=user_id, dry_run=dry_run)
        table.add_row(name, diff.summary(), str(len(diff.skipped_local)))
    console.print(table)
    if dry_run:
        console.print("Dry run: nothing was changed.")


# ------------------------------------------------------------------------------ status


@app.command()
def status(
    coverage: Annotated[
        bool, typer.Option("--coverage", help="Evidence coverage per source.")
    ] = False,
) -> None:
    """Library overview."""
    settings = _settings()
    conn = _db(settings)
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    rows = [
        ("database", f"'{settings.db_path}'"),
        ("tracks", "select count(*) from tracks"),
        ("  local files (not sortable via API)", "select count(*) from tracks where is_local=1"),
        ("  with ISRC", "select count(*) from tracks where isrc is not null"),
        ("  detected remixes/edits", "select count(*) from tracks where is_remix=1"),
        ("artists", "select count(*) from artists"),
        ("albums", "select count(*) from albums"),
        (
            "  with label",
            "select count(*) from albums where label is not null and label<>''",
        ),
        ("evidence rows", "select count(*) from evidence"),
        ("managed playlists", "select count(*) from playlists"),
    ]
    table = Table("metric", "value")
    for label, sql in rows:
        table.add_row(label, str(one(f"select {sql}" if sql.startswith("'") else sql)))
    console.print(table)
    if coverage:
        console.print(_coverage_table(conn))


if __name__ == "__main__":  # pragma: no cover
    app()
