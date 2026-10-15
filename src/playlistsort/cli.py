"""Command-line interface."""

from __future__ import annotations

import sqlite3
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from playlistsort import __version__, db
from playlistsort.config import Settings, load_settings
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
app.add_typer(auth_app, name="auth")
app.add_typer(taxonomy_app, name="taxonomy")

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
        total = one("select count(*) from tracks") or 1
        rows = conn.execute(
            "select source, count(distinct track_id) as n from evidence group by source order by n desc"
        ).fetchall()
        cov = Table("source", "tracks with evidence", "coverage")
        for r in rows:
            cov.add_row(r["source"], str(r["n"]), f"{100 * r['n'] / total:.1f}%")
        console.print(cov if rows else "No evidence yet (Milestone 2).")


if __name__ == "__main__":  # pragma: no cover
    app()
