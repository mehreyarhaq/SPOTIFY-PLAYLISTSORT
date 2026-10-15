# CLAUDE.md — working notes for this repository

## What this is
A Python CLI (`playlistsort`) that sorts a DJ's Spotify Liked Songs into private Spotify playlists
by fine-grained genre (Beatport taxonomy) and, later, by feel. The approved plan lives in the
Claude plan file and is mirrored by `README.md` (user view) and `DECISIONS.md` (why).

## Vocabulary
- Say **playlist** in all user-facing text, docs and CLI help. Never "crate".
- Internally an output class is a `category` on an `axis` (`genre` or `feel`).
- A **canonical tag** is a node in `config/taxonomy.yaml` (Beatport names). An **alias** maps a
  source's raw tag to a canonical tag. **Evidence** is one raw tag from one source for one track.

## Layout
```
src/playlistsort/
  config.py            settings (.env + config/*.yaml)
  db.py                SQLite connection + migrations (db_migrations/NNN_*.sql)
  titles.py            parse "Title - X Remix" into base title / mix name / remixers
  labels.py            review CSV export, label import (seed / between / negative), resolve_category
  spotify/auth.py      PKCE login, --paste-redirect for headless use, token file
  spotify/ingest.py    /me/tracks -> tracks/artists/albums
  spotify/publish.py   plan_publish / apply_publish / smoke_test (idempotent, dry-run)
  taxonomy/normalize.py  raw tag -> canonical (at score time, never at fetch time)
  enrich/base.py       Http (cached, rate-spaced, retrying), TrackRow/load_tracks, EnrichStats
  enrich/match.py      asymmetric fuzzy matcher (remix-aware); pick_best flags ambiguity
  enrich/factory.py    build_enricher(name, settings, conn); ORDER = sonovault, beatport, discogs, deezer
  enrich/<source>.py   one source per module; each writes Evidence rows, never canonical tags
  model/scorer.py      evidence -> genre probabilities (canonicalises at score time)
  model/assign.py      probabilities -> playlist memberships (primary / subgenre / parent / secondary)
  model/classifier.py  personalised logistic regression on embeddings + signal features + evidence
                       prior; soft labels, GroupKFold by artist, user labels honoured at prediction
  review/app.py        Streamlit labelling UI (`playlistsort review --ui`)
  audio/features.py    librosa descriptors: BPM (octave-folded), key/Camelot, loudness, sub-bass, H/P, pump
  audio/acquire.py     rekordbox XML + folder scan + remix-aware matching; Deezer preview download
  audio/embed.py       Backend protocol (maest, clap, fake); embeddings per input_kind; priors -> evidence
  cli.py               typer app
config/taxonomy.yaml   canonical tree + aliases     config/weights.yaml  source weights
docs/research/         research reports + reference files
tests/                 unit tests (no network); tests/cassettes for recorded HTTP
data/                  gitignored: library.sqlite, tokens/, audio/
```

## Commands
- `make setup` / `make setup-audio` / `make setup-models` / `make check` (ruff + pytest) / `make fmt`
- `uv run playlistsort --help`
- Run a single test: `uv run pytest tests/test_titles.py -q`

## Rules
- **TDD**: write the failing test first for every module with logic (titles, normalize, publish
  diff, scorer, db). Unit tests must not touch the network; HTTP goes through vcrpy cassettes.
- **Spotify ToS**: Spotify data is for identity and playlist writing only. Never train on it,
  never scrape `open.spotify.com`, never store `preview_url` tricks. Playlists default to private.
- **Caching**: every external call is cached in `http_cache` (negative results for 30 days).
  Never store signed/expiring URLs; store downloaded file paths.
- **Taxonomy drift**: store `raw_value` + `source_tag_id` on evidence; canonicalise when scoring
  against the current `config/taxonomy.yaml`. Beatport renames genres several times a year.
- **Remixes**: match by ISRC first; a fuzzy candidate with a mix name only matches when our parsed
  mix name agrees; release-level evidence (Discogs) is down-weighted for remixes.
- **Bulk endpoints are gone** in Spotify Dev Mode: one artist/album per call, so cache and batch
  by playlist.
- Keep modules single-purpose; prefer pure functions (diff planning, scoring) that take data in
  and return data out, with thin I/O wrappers around them.
- Commit messages: imperative, explain why. Never include model identifiers in commits or code.

## Milestone order (do not skip ahead)
0 scaffold → 1 Spotify smoke test + ingest → 2 enrichment (SonoVault → Beatport → Discogs → Deezer)
→ 3 first-pass genre playlists + review CSV → 4 audio (previews in cloud, local files on Mac)
→ 5 personalised classifier + Streamlit review → 6 feel + `query` → 7 `sync`.
