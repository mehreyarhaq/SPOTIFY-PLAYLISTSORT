# Handoff notes for the next maintainer (human or AI)

Written 2026-11-11 at the end of the first implementation pass. Read this first, then
`README.md` (how to run), `CLAUDE.md` (conventions), `DECISIONS.md` (why), `docs/PLAN.md`
(the approved plan with the research summaries and verification criteria) and `docs/research/`
(the three research reports with sources, plus reference files such as Beatport's taxonomy
dictionary and a working Beatport v4 auth example).

## 1. What this is

`playlistsort` is a Python CLI that sorts a DJ's ~2000 Spotify Liked Songs (house/techno
family) into private Spotify playlists by fine-grained genre (Beatport's taxonomy: Afro House ›
Afro Melodic, Tech House, Minimal / Deep Tech › Deep Tech, Melodic House & Techno › Melodic
Techno, Indie Dance, ...) and by "feel" (deep & slow, rolling groove, melodic journey,
peak-time driving), and lets the user ask for a playlist by genre, feel or both.

The design principle that must survive: **public tags and audio features are evidence, the
user's own labels are the truth.** Sources disagree, tracks sit between genres, remixes differ
from originals. The system keeps every vote with a weight, says "between" instead of guessing,
and trains a small classifier on the user's labels so playlists follow *their* mixing boundaries.

The user is a DJ, Spotify Premium, has a Beatport account, uses rekordbox and plain folders for
local files, works on an Apple Silicon Mac, and ran this project from a Claude Code cloud
container. They did not know the word "crate": say **playlist** in everything user-facing.

## 2. Current state

Branch `claude/cloud-session-question-ie4mru`, 202 unit tests, `ruff` clean, CI = ruff + pytest
with the `analysis` and `ml` extras. Note: commits `8f67c27` through `0f0934d` were missing the
whole `src/playlistsort/audio/` package on GitHub because an unanchored `audio/` rule in
`.gitignore` (meant for downloaded music under `data/`) also matched the source directory; CI
was red for that span. Fixed in the commit that adds the package together with this file.

| Milestone | Code | Live-verified |
|---|---|---|
| 0 Scaffold, docs, taxonomy, research | done | n/a |
| 1 Spotify PKCE login, ingest, publish + smoke test | done | **no** |
| 2 Enrichment: SonoVault → Beatport → Discogs → Deezer | done | **no** |
| 3 Scorer, assignment, review CSV, first genre playlists | done | **no** |
| 4 Audio: local matching, previews, features, embeddings | done | features yes (synthetic audio); models **no** |
| 5 Personalised classifier, model-aware assign, Streamlit review | done | classifier yes (synthetic); UI **no** |
| 6 Feel axes, `query` | done | synthetic only |
| 7 `sync` | done | **no** |

**No request has ever reached Spotify, Beatport, SonoVault, Discogs or Deezer.** The cloud
container's network policy blocked every external host (verified 2026-10-15), and the user had
not yet created the Spotify developer app or set any keys. All HTTP code was tested against
mocked transports shaped from documentation and reference code. Treat the first live run as a
debugging session, not a victory lap.

## 3. Assumptions to verify on the first live run

In the order the pipeline hits them. Each names the file to fix if wrong.

1. **Spotify Dev Mode can create and fill playlists.** Two 2026 community reports mention 403 /
   405 on `POST /me/playlists` and `/playlists/{id}/items`. `publish --smoke-test`
   (`spotify/publish.py::smoke_test`) exercises create, add, read-back, replace, remove, delete
   and reports each step. Run it before anything else.
2. **Saved-track items still carry `external_ids.isrc`** (removal was announced Feb 2026 and
   reverted Mar 2026). `spotify/ingest.py::parse_saved_item`. The smoke test logs it.
3. **`playlist_items` entries use `item` or `track`.** `publish._entry_uri` accepts both.
4. **Artist `genres` are mostly empty** (expected since 2025). `ingest --artist-genres` is off by
   default; they are weight 0.3 evidence at most.
5. **SonoVault**: base URL `https://api.sonovault.now/v1` and `POST /tracks/resolve` with
   `{"input_type": "spotify_id", "items": [{"spotify_id": ...}]}` are inferred from the
   TypeScript client's README (`docs/research/reference/sonovault_README.md`), not from the API
   docs, which were unreachable. Check `sonovault.now/docs`; override with `SONOVAULT_BASE_URL`;
   fix the body in `enrich/sonovault.py`. Also check whether credits are per item or per request
   before resolving all 2000 tracks (free tier: 1000 requests/month).
6. **Beatport auth**: `enrich/beatport.py::BeatportAuth` implements the account-login flow
   used by `beets-beatport4` and fomoplayer (`docs/research/reference/beatport_v4_auth_example.js`):
   `POST account.beatport.com/identity/v1/login/` → `GET /o/authorize/` → `POST /o/token/` with
   the public docs-app client id. Beatport can close this route. The pasted-token path
   (`BEATPORT_BEARER_TOKEN`) is the fallback. The track object field names (`genre`, `sub_genre`,
   `bpm`, `key.camelot_number/letter`, `mix_name`, `remixers`, `release.label`, `length_ms`)
   come from community dumps; adjust `evidence_from_track` if they differ.
7. **Discogs search** uses `track=` + `artist=` with `type=release`; if recall is poor try
   `release_title=` or `q=`. 60 requests/minute with a token (`enrich/discogs.py`).
8. **Deezer** `GET /track/isrc:{ISRC}` (colon form) returns 200 with an `error` object for
   misses (`enrich/deezer.py::_clean`). Preview URLs are signed; `preview_url()` fetches live.
9. **MAEST / CLAP** (`audio/embed.py`): the MAEST hidden-state index (layer 7) and the
   `AutoFeatureExtractor` kwargs, and CLAP's `ClapProcessor(audios=...)` signature, have not
   been executed. Install with `make setup-models` on the Mac and run `audio embed --limit 5`.
10. **Essentia** is declared as an extra but no code uses it; the PyTorch/librosa path is the
    only implemented one (deliberate: no Apple-Silicon or Windows wheels).

## 4. What the user must provide

- Spotify **Premium** (Dev-Mode apps require a Premium owner since Feb 2026).
- A Spotify developer app at developer.spotify.com/dashboard with redirect URI exactly
  `http://127.0.0.1:8888/callback`; its client ID in `.env` (copy `.env.example`).
- Keys for Milestone 2: SonoVault API key (free), Discogs personal token, Beatport username +
  password or a pasted bearer token.
- If running in a Claude Code cloud environment: network allowlist (or full access) for
  `api.spotify.com accounts.spotify.com account.beatport.com api.beatport.com sonovault.now
  api.sonovault.now api.discogs.com api.deezer.com cdnt-preview.dzcdn.net musicbrainz.org
  ws.audioscrobbler.com api.reccobeats.com huggingface.co cdn-lfs.huggingface.co
  cdn-lfs-us-1.hf.co itunes.apple.com audio-ssl.itunes.apple.com` with package managers allowed.
  Note that the preview CDN host varies (`*.dzcdn.net`); a wildcard needs the Settings UI.
- For feel accuracy: the tracks downloaded locally (rekordbox XML export or folders). Previews
  are fine for genre but bias energy upward.

## 5. First-run sequence and what "good" looks like

```
make setup                                   # or: uv sync --group dev
uv run playlistsort auth login --paste-redirect   # headless; plain `auth login` opens a browser
uv run playlistsort publish --smoke-test     # every step "yes"; first_isrc not MISSING
uv run playlistsort ingest                   # ~2000 tracks; `status` shows ISRC on most, labels on albums
uv run playlistsort enrich                   # coverage table: aim for >70% tracks with a Beatport genre
uv run playlistsort taxonomy unmapped        # add aliases to config/taxonomy.yaml for anything listed
uv run playlistsort taxonomy pull            # Beatport's live tree vs config; add missing genres
uv run playlistsort score                    # status counts: ok / between / unknown
uv run playlistsort review                   # the uncertain tracks
uv run playlistsort assign                   # evidence-only run
uv run playlistsort publish --dry-run        # inspect, then
uv run playlistsort publish                  # private "[PS] ..." playlists appear in Spotify
```
Then labels: `labels export review.csv --only review`, the user fills `your_genre` (and `also`
for between, `not X` for negatives), `labels import review.csv`; or `make setup-ui` and
`review --ui`. Then on the Mac: `make setup-audio`, `audio match-local` (set `REKORDBOX_XML` /
`LOCAL_MUSIC_DIRS`), `audio fetch-previews` for the rest, `audio analyze`, `make setup-models`,
`audio embed --model maest`, `audio embed --model clap`. Then `train` (needs ≥ 2 categories with
≥ 3 labels; aim for 20–30 labels per playlist), `assign` (now model-based), `publish`. Feel:
`assign --axis feel`, `publish --axis feel`, `query --genre "Tech House" --feel "peak-time driving"`.
Finally `sync` on a schedule.

Expectations from the research: 10–25% of a DJ's liked songs are in no catalogue (white labels,
SoundCloud-only edits) and need audio + review; tech house vs minimal and progressive vs melodic
overlap acoustically, so "between" is a correct answer there, not a bug.

## 6. Architecture

Data flow: Spotify Liked Songs → `tracks/artists/albums` → enrichers write **evidence rows**
(raw tag, source, match method) → `model/scorer.py` canonicalises them against
`config/taxonomy.yaml` **at score time** and fuses them with `config/weights.yaml` into genre
probabilities → `model/assign.py` turns probabilities into playlist memberships (primary,
sub-genre, parent, secondary-when-between) stored under a run id → `spotify/publish.py` diffs
against the real playlists and writes idempotently. Labels (`labels.py`, `review/app.py`) feed
`model/classifier.py`, which replaces the evidence-only probabilities once trained; the user's
labels are honoured verbatim at prediction. Audio (`audio/`) adds signal features, embeddings
and Discogs-style priors (as evidence from source `audio_discogs400`). `model/feel.py` derives
library-relative feel axes; `model/query.py` selects by genre/feel; `pipeline.py` composes
everything; `cli.py` is thin.

Modules are listed with one-line responsibilities in `CLAUDE.md`. SQLite tables
(`db_migrations/001_core.sql`, `002_audio.sql`): `tracks`, `artists`, `track_artists`,
`albums`, `evidence` (unique per track/source/field/raw_value), `taxonomy_snapshots`,
`http_cache` (positive 1 year, 404s 30 days), `labels` (append-only sessions), `assignments`,
`playlists` (source of truth for publishing), `audio_sources` (local paths only), `features`,
`embeddings` (keyed by model, version, input kind).

Config: `config/taxonomy.yaml` (37 genres, 38 sub-genres, ~130 aliases; broad tags vote
family-only; `sources:` restricts an alias), `config/weights.yaml` (source weights, match caps,
`assignment.threshold`, `review_margin`, `min_evidence_weight`).

## 7. Rules that must survive (see `CLAUDE.md`)

Playlist, never crate. Spotify data is identity + output only: never train on it, never scrape
`open.spotify.com`, playlists private by default. Cache every external call; never store signed
URLs. Canonicalise at score time, never at fetch time. ISRC first; a fuzzy candidate with a mix
name matches only when our parsed mix name agrees; release-level evidence is down-weighted for
remixes. TDD with no network in unit tests (mocked `httpx` transports; `tests/fake_spotify.py`
for the publisher). Ruff line length 100 and pep8-naming are enforced in CI.

## 8. Known gaps and debts

- `labels export` has no `--axis` option (import does); feel labelling is CSV-only.
- `review --ui` is genre-only and has never been run; `streamlit` is in the `ui` extra.
- `sync` runs ingest → enrich → assign → publish but no audio steps.
- `publish` picks the latest run by `max(run_id)` string; run ids are UTC timestamps, so custom
  names can sort wrongly. Pass `--run-id` explicitly when in doubt.
- `ingest --limit` also caps the album-label fetch.
- `taxonomy pull` requires Beatport auth.
- Title parser: a remix whose remixer appears only in Spotify's `artists` list (not the title)
  gets `remixers=()` and confidence 0.8; "(Hot Creations)"-style label parentheticals are kept
  in the base title with confidence 0.7. 60 real-world cases in `tests/fixtures/titles.csv`.
- Local-file matching shortlists candidates by title similarity only (rapidfuzz), then applies
  the remix-aware matcher; files without tags fall back to "Artist - Title" filenames.
- Classifier: GroupKFold needs ≥ 2 primary-artist groups; negatives are not used in training,
  only at prediction (they zero the category). `predict_library` raises if the feature layout
  changed since training (e.g. a new embedding model): retrain.
- Feel buckets are percentile-based (library-relative) and fixed thresholds; a trained feel
  model (`train --axis feel`) replaces them once labels exist.
- CI installs `analysis` + `ml` extras only; `models` (PyTorch) and `ui` are untested in CI.
- Beatport sub-genre numeric ids in `taxonomy.yaml` partly come from an old dump; `taxonomy pull`
  stores the live tree in `taxonomy_snapshots` for reconciliation.

## 9. Suggested order of work

1. Open network access or run on the Mac; create the Spotify app; `auth login`; **smoke test**.
2. `ingest`, then `enrich --source sonovault --limit 50` and inspect the raw evidence before
   spending credits; fix the request shape if needed; then Beatport, Discogs, Deezer.
3. `taxonomy unmapped` / `taxonomy pull`; extend aliases.
4. `score`, `assign`, `publish --dry-run`, `publish`; let the user listen to the first playlists.
5. Collect ~20 labels per playlist (CSV or UI); `train`; compare the confusion matrix with the
   known-fuzzy pairs; `assign`; `publish`.
6. Audio on the Mac; re-`train` with embeddings; feel; `query`; schedule `sync`.

## 10. Environment notes

Python 3.12 pinned in `.python-version` (3.13 works); `uv` manages extras: `analysis`
(librosa, soundfile, mutagen, scipy), `ml` (scikit-learn, pandas), `models` (torch,
transformers), `ui` (streamlit), `essentia` (Linux x86_64 only, unused). `make setup-*` targets
exist for each. Apple Silicon: use the PyTorch path; `essentia-tensorflow` wheels are reported
broken there. Data lives under `data/` (gitignored): `library.sqlite`, `tokens/spotify.json`
(portable between machines), `tokens/beatport.json`, `audio/`, `models/`.
