<!-- Copied from the approving session's plan file on 2026-11-11 so it travels with the repo. -->

# Plan: sort ~2000 Liked Songs into DJ-grade genre (then feel) playlists

## Context

The user DJs electronic music (house/techno family) and has ~2000 tracks in Spotify Liked
Songs with no playlists. They want them sorted automatically, accurately enough to mix from,
along two independent axes: **genre** at DJ granularity (afro house vs tech house vs deep
tech vs minimal vs melodic vs indie dance ...) and **feel** (slow/deep vs rolling groove vs
peak-time driving). End goal in their words: "specify a genre, or a feel, or a combination
of both and get an output playlist." Genre first.

Past attempts failed on six concrete problems; each becomes a requirement:

| # | Failure the user hit before | Design response |
|---|---|---|
| F1 | Tags too broad ("electronic") or wrong | Per-track sources with a DJ taxonomy (Beatport first); artist-level tags are weak priors (weight ≤ 0.3) |
| F2 | Tracks sit between two genres | Soft labels + probabilities; "between A and B" is a first-class state; low-margin tracks go to review |
| F3 | Sources disagree | Evidence table keeps every vote with a weight; disagreement is surfaced, the user's label wins |
| F4 | Broad-tag platforms vs specific-tag platforms | Broad tags may only vote for a parent genre, never a leaf |
| F5 | Different names for the same genre | Alias table maps every source's vocabulary into one canonical tree |
| F6 | Remixes inherit the original's genre | ISRC-first matching, mix-name-aware title parsing, per-track Beatport genre, release-level sources down-weighted for remixes, audio analysed per recording |

And the structural fix for "whose definition of tech house?": the final classifier is
**personalised**. The user labels a seed set; public tags + audio features are inputs; the
user's playlists are the output classes.

## Decisions made with the user

- Audio: currently Spotify-only; user can download ~90% locally (rekordbox and/or plain folders).
- Output: private Spotify playlists via the Web API; later a query interface (genre and/or feel -> playlist).
- Playlist set: **one playlist per Beatport genre present in the library, plus Beatport sub-genre playlists** (e.g. Afro House > Afro / Latin, Afro Melodic, 3Step). User may rename/merge/add personal ones later; their labels reshape boundaries.
- Labelling: user will provide training data; amount decided after the first automatic pass.
- Spotify: **Premium** account, no developer app yet -> create a new Dev-Mode app (so no Audio Features / previews from Spotify).
- Beatport: user **has an account** -> Beatport v4 API via user login is in scope.
- Run location: **this cloud environment first** (user will open network access), Mac (Apple Silicon) as fallback and for local-file audio analysis.
- Budget: "whatever works best"; research ruled out the expensive option (Cyanite) on price/coverage anyway.
- Vocabulary: say **"playlist"** in all user-facing text and docs (not "crate"). Internally the output class is called `category`.

## Hard constraints discovered

- Repo is empty (no commits; remote empty). Container: Python 3.13.16, uv 0.11.32, node 22, ffmpeg 6.1; system Python externally managed -> `uv` venv.
- Cloud network policy currently **blocks every host the pipeline needs** (verified 2026-10-15: Spotify, Deezer, iTunes, Discogs, Beatport, MusicBrainz, Last.fm, HuggingFace, Essentia, ReccoBeats all 403 at the proxy). Only package registries pass.
- The linked Spotify MCP connector cannot enumerate Liked Songs (natural-language search, ≤ 20 results; a test query returned a radio playlist) nor build a playlist from an explicit track list -> unusable for the bulk pipeline.
- Headless container: the PKCE loopback redirect cannot open a browser here -> paste-redirect login flow required (see M1).

## Research findings (2026-10-15, primary sources; full reports go to `docs/research/` at M0)

### R1. Spotify Web API — what a new Dev-Mode app can still do
- **Available:** `GET /me/tracks` (50/page, scope `user-library-read`) with `external_ids.isrc` (removal announced Feb 2026 was reverted Mar 2026), artists, album, duration, explicit, `is_local`. `GET /albums/{id}` -> `label`. `GET /artists/{id}` (one per call; bulk "Get Several" endpoints removed Feb 2026); `genres` is deprecated and since Jan 2025 sparse/broad/often empty. Playlists: `POST /me/playlists`, `POST/PUT/DELETE /playlists/{id}/items` (100 URIs per call; path renamed from `/tracks`), `GET /playlists/{id}/items` for own playlists; scopes `playlist-modify-private/public`; ~10k tracks per playlist; folders not accessible. `/search` capped at 10 results.
- **Gone for good:** Audio Features, Audio Analysis, Recommendations, Related Artists, `preview_url`, `popularity`.
- **Dev Mode rules (Feb–Jul 2026):** owner must be Premium; 5 users; 25 client IDs sharing one quota; 429 + `Retry-After`; redirect URI must be loopback `http://127.0.0.1:PORT` with PKCE. Single-user "manage my own account" is the intended use.
- **Client:** `spotipy >= 2.26.0` (Mar 2026) already targets the new paths and ships `SpotifyPKCE`.
- **ToS to respect:** no ingesting Spotify content into ML models, no long-term caching of Spotify data beyond need, no scraping of `open.spotify.com/embed`. Design response: Spotify data is used only for identity/joins and to write playlists; audio comes from the user's files or third-party previews; the classifier trains on user labels + third-party metadata + audio features.
- **Bonus:** since Sept 2025 Premium users see their own Spotify playlists inside rekordbox, Serato, djay (and since Sept 2026 VirtualDJ, Cross DJ, edjing) in 51 markets. Our playlists are ordinary user playlists, so they appear there.
- **Unconfirmed, test first:** two 2026 community reports of 403/405 on playlist create/add in Dev Mode.

### R2. Track-level genre sources and taxonomy
- **Beatport (backbone).** Verified live: every track row, incl. each remix/edit, has its own genre, sub-genre, BPM, key. Current tree relevant to the user: Afro House (Afro / Latin, Afro Melodic, 3Step — **no "Afro Tech" on Beatport**), Tech House (Latin Tech), Minimal / Deep Tech (Deep Tech, Minimal House, Bounce), Melodic House & Techno (Melodic House, Melodic Techno), Indie Dance (Dark Disco), Indie Tech (new Oct 2026), Organic House (split from Downtempo Apr 2025), Progressive House, Deep House, House (Acid, Soulful), Jackin House, Funky House, Nu Disco / Disco, Techno (Peak Time / Driving: Peak Time, Driving, Psy-Techno), Techno (Raw / Deep / Hypnotic: Raw, Deep / Hypnotic, Dub, Broken, EBM), Hard Techno, Electro, Downtempo, Electronica, Amapiano, UK Garage / Bassline, Breaks, Trance, Mainstage, Dance / Pop. Taxonomy drifts several times a year with back-catalogue reclassification.
- **Beatport access:** official v4 API is partner-gated (apply in parallel). Working community route (used by `beets-beatport4` 1.3.0, Sept 2026, and fomoplayer): `POST account.beatport.com/identity/v1/login/` with the user's username+password using the public client ID of Beatport's docs app -> `/o/authorize/` -> `/o/token/`; ~10-minute access tokens with refresh. Alternative: paste a bearer token from the browser. Endpoints: `GET /v4/catalog/tracks/?isrc=` (verified Aug 2026), `/catalog/tracks/?id=`, `/catalog/search/?q=&type=tracks`, `/catalog/genres/` (with `sub_genres`). Track objects: `genre`, `sub_genre`, `bpm`, `key{name,camelot}`, `isrc`, `mix_name`, `remixers`, `release.label`. No published rate limit -> 1–2 req/s + backoff, one pass, cache everything. Website scraping is dead (Cloudflare). Beatport can close this route; keep volume low.
- **SonoVault** (Rekordcloud/Lexicon makers): REST, free key 1000 req/month (20/min); bulk `resolve` (100 items/request) by `spotify_id`/ISRC returns `genre[]`, `subgenre[]`, label, `beatport_id`, `discogs_id`. Turns Beatport search into exact ID lookups. Verify credits-per-item vs per-request and taxonomy quality on 50 known tracks before relying on it; Starter tier €29 if free credits run out.
- **Discogs** (free token, 60 req/min, mandatory User-Agent): release-level `styles` (Tech House, Deep House, Minimal, Minimal Techno, Microhouse, Progressive House, Tribal House, Dub/Deep Techno, UK Garage, Nu-Disco, Balearic, Downtempo, Leftfield ...; **no** Afro House / Melodic / Organic / Indie Dance / Deep Tech). `search?track=` pins a remix to a release.
- **Deezer:** `GET https://api.deezer.com/track/isrc:{ISRC}` (colon form) -> `preview` (30 s MP3, signed/expiring URL; sometimes empty), coarse album genre; no auth; ~50 req/5 s.
- **Low-weight tie-breakers (deferred past M3):** MusicBrainz (1 req/s; sparse per-recording genres), Last.fm top tags (noisy), Spotify artist genres, ReccoBeats (free Spotify-style audio features by Spotify ID, coverage inconsistent). iTunes: `lookup?isrc=` is dead; `search?term=` previews only as last resort (promotional-use terms).
- **Label prior:** no dataset exists; build from our own enriched tracks (genre distribution per `release.label`).
- **Commercial tagging:** Cyanite API €290/month and lacks afro/melodic categories -> skip. Replicate hosts `mtg/effnet-discogs` at ≈ $0.00022/run (≈ $0.44 for 2000 tracks) -> cheap cloud fallback if local models won't install.
- **Reusable:** `metabrainz/genre-matching` crosswalk CSVs, `zabe40/musicbrainz-userscripts` (full Beatport genre|sub-genre -> MusicBrainz map), `hastefuI/discogs-dataset-genres-styles` (CC0), `beets-beatport4` (auth/client code), `ccombe/beatport-collector` (ISRC-first matching), idanbeck `beatport-skill` pattern (ISRC first, then asymmetric fuzzy artist+title+mix, split into auto vs needs-review piles), `Heyian/spotify-genres` (Oct 2026: MusicBrainz + Discogs-EffNet on Deezer previews). Reference files saved by the research (Beatport taxonomy dictionary, Discogs style list, crosswalks, SonoVault schema, working v4 auth JS) sit in the session scratchpad `dl/` and get copied to `docs/research/reference/`.
- **Coverage expectation:** 10–25% of a DJ's liked songs (white labels, SoundCloud-only edits, unreleased) are in no catalogue -> audio path and review step are mandatory.

### R3. Audio analysis for subgenre + feel
- **Off-the-shelf genre heads do not cover the user's categories.** Essentia `genre_discogs400` / MAEST 519 know Tech House, Deep House, Minimal, Minimal Techno, Progressive House, Tribal House, Techno, Deep/Dub Techno, Nu-Disco, UK Garage ... but **not** Afro House, Melodic House & Techno, Indie Dance, Organic House, Peak-time vs Raw. -> Pretrained models supply **embeddings and priors**; the final head is trained on the user's labels.
- **Evidence that shapes the design:** Hsu et al. 2022 (30 Beatport subgenres): 50% from a 30 s excerpt vs 55% from 120 s; tempogram features lift to 61%. EPFL 2026 "acoustic overspecification" (3500 Beatport tracks, MIT code `alunxu/edm-overspec`): only 17–20 acoustic families; **tech house vs minimal and progressive vs melodic collapse acoustically**; afro/organic/peak techno/downtempo form one gradient; hand-made EDM features (tempogram, sub-bass ratio, sidechain-pump proxy, H/P ratio, crest factor) cluster EDM better than MERT/CLAP. HouseX-v2 2025: expert **soft labels** beat hard labels; audio LLMs score near chance -> no LLM audio tagging. Ma & Lerch 2024: a logistic-regression probe on frozen embeddings reaches near-SOTA with **~20 labels per class**; concatenating heterogeneous embeddings helps.
- **Models (CC BY-NC-SA, fine for personal use):** MAEST `mtg-upf/discogs-maest-30s-pw-129e` via `transformers` (embeddings + Discogs400 head, CPU-fast); LAION CLAP music (512-d) for similarity; optional Discogs-EffNet + Essentia feel heads (danceability, mood, engagement, voice/instrumental, timbre) where Essentia installs; BPM via DeepRhythm (95.9% acc, 0.12 s/track CPU) with octave folding into 110–140; key via Essentia `KeyExtractor(profileType='edma')` or librosa fallback; RMS/crest, sub-bass ratio, sidechain-pump proxy, H/P ratio, cyclic-tempogram concentration ported from edm-overspec.
- **Install reality:** `essentia-tensorflow==2.1b6.dev1389` is the last wheel for Python 3.11–3.13 (Linux x86_64 fine; macOS 15 arm64 wheel reported broken for TF algorithms; no Windows wheels). -> PyTorch path is primary everywhere; Essentia optional behind an extra on Linux.
- **30 s previews:** adequate for genre (drop-centred), **bias energy upward** and hide intros/outros -> prefer local files for feel.
- **Audio acquisition order:** local files (`pyrekordbox` 0.5.0 XML/db + folder scan via `mutagen`, matched by ISRC/tags/fuzzy title+duration) > Deezer preview by ISRC > iTunes search preview > never Spotify embed scraping.
- **Feel for DJs:** Mixed In Key energy 1–10 (transient/ambient profile), BPM, key (Camelot), vocal presence, dark/bright, groove vs driving. Design: a few interpretable axes learned/calibrated from user labels + CLAP/MAEST kNN for "more like this"; BPM and key order tracks **inside** a playlist, they do not define it.
- **Compute:** 2000 × 30 s ≈ 1–3 h CPU per backbone; cache every feature/embedding.
- **Reusable:** DataJockey (a DJ's 4k-track library on EffNet embeddings + Streamlit), AudioMuse-AI (AGPL), Essentia-to-Metadata (MIT), reklawdbox (audio + metadata fusion with confidence capping), housex-v2 (soft-label recipe).

## Recommended approach

Separate *what the world calls a track* from *what the user mixes it as*:

1. **Canonical tags**: Beatport's genre/sub-genre tree is the canonical vocabulary; every other source maps in through an alias table (`config/taxonomy.yaml`). Broad tags vote parent-only. Canonicalisation happens **at score time** against a versioned taxonomy snapshot, never at fetch time (Beatport renames genres several times a year).
2. **Evidence table**: every tag from every source, per track, with `raw_value`, `source_tag_id`, `match_method` (`isrc|id|fuzzy|fuzzy_ambiguous`), `match_score`. Weights (in `config/weights.yaml`): Beatport track genre 1.0 · SonoVault 0.8 · Discogs release style 0.7 (× 0.6 when the track is a remix by a non-release artist) · Discogs400 audio prior 0.5 · label prior 0.4 · MusicBrainz/Last.fm 0.4 · Spotify artist genre 0.3 · broad tags 0.1 parent-only · any `fuzzy_ambiguous` match capped at 0.3.
3. **Audio features + embeddings** of the actual recording (M4), stored with `input_kind` (preview vs full), `offset_s`, `model_version` so preview and full-file embeddings are never mixed silently.
4. **Categories** (= playlists): first pass from evidence alone (M3); then a personalised per-axis classifier (logistic regression / small MLP over MAEST ⊕ CLAP ⊕ EDM features ⊕ evidence prior, trained on soft labels incl. negatives and "between" pairs; `GroupKFold` by primary artist to avoid leakage) with multi-membership above a threshold and a low-margin review queue (M5).
5. **Idempotent publisher** + **query CLI** writing private playlists named `[PS] <Genre>` / `[PS] <Genre> › <Sub-genre>`; the `playlists` table is the source of truth (not description markers).

```
Spotify Liked Songs ──ingest──> SQLite library (tracks, ISRC, artists, albums+label, parsed mix name)
        │                                   │
        ▼                                   ▼
 metadata enrichers (M2)             audio acquisition (M4)
 SonoVault -> Beatport -> Discogs    Deezer preview by ISRC (cloud) ; local files via rekordbox/folders (Mac)
 Deezer (ISRC confirm)                      │
        │                                   ▼
        ▼                            audio analysis, cached (M4)
 taxonomy normaliser (score time)    MAEST + CLAP embeddings · Discogs400 priors · BPM · key · EDM hand features
        │                                   │
        └──────────> evidence + features per track <──────────┘
                                 │
                scorer (M3): canonical prior distribution + agreement score
                                 │
          ┌──────────────────────┴──────────────────────┐
          ▼                                             ▼
 review: CSV export/import (M3), Streamlit (M5)   personalised classifier (M5)
 seeds, lowest-margin, "between A/B", negatives ─>  soft labels, multi-membership
                                                        │
                              assignments (prob per category) -> publisher / query (M3+)
```

### Components (`src/playlistsort/`, one responsibility each)

| Module | Does | Key deps |
|---|---|---|
| `config.py` | settings from `.env` + `config/*.yaml`; data dir | pydantic-settings |
| `db.py` + `db_migrations/001_core.sql` (M0), `002_audio.sql` (M4) | SQLite schema, migrations, thin repository functions | sqlite3 |
| `spotify/auth.py` | PKCE login; `--paste-redirect` headless mode (prints URL, accepts pasted redirect); token file `data/tokens/spotify.json` portable between cloud and Mac | spotipy ≥ 2.26 |
| `spotify/ingest.py` | page `/me/tracks`; parse titles; fetch album label (cached, one call each) | auth, db, titles |
| `spotify/publish.py` | `plan_publish` (ordered diff per category) / `apply_publish` (dry-run; verify playlist exists, recreate if deleted; `PUT` replace when diff > 50% else add/remove in 100-URI chunks; skip `is_local`) / `smoke_test` | auth, db |
| `titles.py` | `parse_title(title, artists) -> ParsedTitle(base_title, mix_name, mix_type, remixers, featuring, is_remix, confidence)`; handles dash form "Title - X Remix", parenthesised labels "(Hot Creations)" false positives, remixer appearing in `artists` rather than the title | rapidfuzz |
| `taxonomy/normalize.py` | `normalize(raw, source, *, taxonomy, source_tag_id) -> Canonical(tag, parent, specificity, via_alias) | None`; unknown raw tags are recorded for `taxonomy unmapped` | pyyaml |
| `enrich/base.py` | `Evidence` dataclass; `Enricher` protocol (`lookup`, `batch_lookup`); shared httpx client with retry/backoff; `http_cache` keyed by `(source, endpoint, params_hash)` with `status` and `expires_at`, **negative results cached 30 days** | httpx, tenacity |
| `enrich/sonovault.py`, `beatport.py`, `discogs.py`, `deezer.py` (M2); `musicbrainz.py`, `lastfm.py`, `reccobeats.py`, `labels.py` (M5+) | one source each; matcher is asymmetric: a candidate with a mix name matches only if our parsed mix name agrees; duration within 3 s; min fuzzy score 0.72 | base |
| `model/scorer.py` | evidence -> canonical prior vector + agreement/entropy per track | numpy |
| `labels.py` | CSV export (track, top-2 categories, evidence summary, preview link) and import; label sessions with `kind` (`seed|review|negative|between`); append-only, latest session wins | — |
| `audio/acquire.py`, `audio/features.py`, `audio/embed.py` (M4) | previews -> `data/audio/`; local matching; features; MAEST/CLAP backends; optional Essentia | torch, transformers, laion_clap, librosa, deeprhythm, pyrekordbox, mutagen |
| `model/classifier.py`, `model/active.py` (M5); `model/feel.py` (M6) | personalised classifier with CV report + confusion matrix; seed/uncertainty sampling; feel axes | scikit-learn |
| `review/app.py` (M5) | Streamlit: play local file/preview, show evidence, pick category(ies)/between/negative | streamlit |
| `cli.py` | typer commands (below) | typer |

### Data model (SQLite `data/library.sqlite`, migration 001)
```
tracks(spotify_id PK, isrc, title, base_title, mix_name, mix_type, remixer, is_remix, title_confidence,
       album_id, duration_ms, explicit, is_local, added_at, raw_json)  INDEX(isrc), INDEX(base_title, remixer)
artists(spotify_id PK, name, genres_json, fetched_at)
track_artists(track_id, artist_id, position, PK(track_id, artist_id))
albums(spotify_id PK, name, label, release_date, fetched_at)
evidence(id PK, track_id, source, field, raw_value, source_tag_id, match_method, match_score, weight,
         meta_json, fetched_at)  UNIQUE(track_id, source, field, raw_value), INDEX(source)
taxonomy_snapshots(source, version, fetched_at, json, PK(source, version))
http_cache(source, endpoint, params_hash, status, body, fetched_at, expires_at, PK(source, endpoint, params_hash))
labels(id PK, session_id, track_id, axis, category, weight, kind, note, created_at)  INDEX(track_id, axis)
assignments(run_id, track_id, axis, category, prob, PK(run_id, track_id, axis, category))
playlists(axis, category, spotify_id, name, snapshot_id, synced_at, PK(axis, category))
schema_version(version)
-- migration 002 (M4): audio_sources(track_id, kind, local_path, duration_s, fetched_at)  [never store signed URLs]
--   features(track_id, bpm, key, camelot, ... , json);  embeddings(track_id, model, model_version, input_kind,
--   offset_s, dim, dtype, blob, UNIQUE(track_id, model, model_version, input_kind))
```

### CLI (typer, `playlistsort ...`)
`auth login [--paste-redirect]` · `auth status` · `ingest [--limit N] [--since]` · `enrich --source {sonovault,beatport,discogs,deezer,all} [--limit] [--refresh]` · `taxonomy pull` · `taxonomy unmapped` · `score [--run-id]` · `labels export FILE` · `labels import FILE` · `assign --axis genre [--threshold] [--margin]` · `publish [--axis] [--category ...] [--dry-run] [--smoke-test]` · `status [--coverage]` · (M4) `audio acquire|analyze` · (M5) `review`, `train` · (M6) `query --genre X --feel Y --name ...` · (M7) `sync`.

## Project setup — Milestone 0 (the "/init" part)

```
pyproject.toml  .python-version (3.12)  README.md  CLAUDE.md  DECISIONS.md  .env.example  .gitignore  Makefile
.github/workflows/ci.yml            # ruff + pytest, no network
config/taxonomy.yaml  config/weights.yaml
docs/research/r1_spotify_api.md  r2_genre_sources.md  r3_audio_ml.md   docs/research/reference/  (scratchpad dl/ copied)
src/playlistsort/{__init__,config,db,titles,labels,cli}.py
src/playlistsort/db_migrations/001_core.sql
src/playlistsort/spotify/{auth,ingest,publish}.py
src/playlistsort/taxonomy/normalize.py
src/playlistsort/enrich/{base,sonovault,beatport,discogs,deezer}.py     # stubs until M2
src/playlistsort/model/scorer.py                                       # M3
tests/{conftest,test_titles,test_normalize,test_publish_diff,test_db,test_scorer}.py
tests/fixtures/titles.csv   tests/cassettes/
data/   # gitignored: library.sqlite, tokens/, audio/
```
- `uv init --package playlistsort`; `requires-python >=3.12,<3.14`; `.python-version` 3.12 (widest torch/essentia wheel coverage). Dependency groups: `core` (spotipy, httpx, tenacity, typer, pydantic-settings, pyyaml, rapidfuzz, numpy), `audio` (torch, transformers, laion_clap, librosa, deeprhythm, soundfile, mutagen, pyrekordbox), `essentia` (Linux only, pinned `2.1b6.dev1389`), `ui` (streamlit, scikit-learn), `dev` (pytest, vcrpy, ruff).
- `CLAUDE.md`: purpose, commands, architecture map, vocabulary ("playlist", not "crate"), ToS rules (no Spotify data in models, no scraping, caching policy), testing rules (unit tests never touch the network).
- `.env.example`: `SPOTIFY_CLIENT_ID`, `SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback`, `BEATPORT_USERNAME`, `BEATPORT_PASSWORD` (or `BEATPORT_BEARER_TOKEN`), `SONOVAULT_API_KEY`, `DISCOGS_TOKEN`, `LOCAL_MUSIC_DIRS`, `REKORDBOX_XML`.
- `README.md`: setup incl. **user actions**: create the Spotify app at developer.spotify.com/dashboard (redirect URI `http://127.0.0.1:8888/callback`, Web API, copy client ID), get a SonoVault key and a Discogs token, and set the environment network allowlist.
- First commit on `claude/cloud-session-question-ie4mru`, pushed.

### Network allowlist for this cloud environment (user action: environment menu in the title bar -> Edit -> Network access)
`api.spotify.com`, `accounts.spotify.com`, `account.beatport.com`, `api.beatport.com`, `sonovault.now` (+ its API host once confirmed), `api.discogs.com`, `api.deezer.com`, `*.dzcdn.net`, `musicbrainz.org`, `ws.audioscrobbler.com`, `api.reccobeats.com`, `huggingface.co`, `cdn-lfs.huggingface.co`, `cdn-lfs-us-1.hf.co`, `itunes.apple.com`, `audio-ssl.itunes.apple.com`; keep "Allow package managers" ticked. Or switch the policy to full access. Steps: https://code.claude.com/docs/en/cloud-environments#network-access

## Milestones (each ends with something the user can see)

| # | Milestone | Deliverable the user sees | Risk retired |
|---|---|---|---|
| 0 | Project setup | scaffold, docs, taxonomy draft, CI green, pushed | — |
| 1 | Spotify round-trip, **smoke test first** | `publish --smoke-test` creates/fills/replaces/deletes a private playlist and logs whether `isrc` and artist `genres` are present; then `ingest` -> 2000 tracks in SQLite with parsed mix names and album labels | Dev-Mode 403/405 reports; headless PKCE |
| 2 | Enrichment, in order **SonoVault -> Beatport -> Discogs -> Deezer (ISRC confirm only)** | `status --coverage`: % tracks with Beatport genre/sub-genre, Discogs style, label; `taxonomy unmapped` list; taxonomy snapshot pulled | catalogue coverage for underground tracks; Beatport auth route |
| 3 | First-pass genre playlists | `score` + `assign` + `publish` -> draft `[PS] <Genre>` / `[PS] <Genre> › <Sub-genre>` playlists from evidence alone; agreement report; `labels export review.csv` with top-2 categories per track; user marks ~10 tracks per category; `labels import` | whether metadata alone is good enough anywhere; taxonomy fit |
| 4 | Audio: **previews in the cloud first**, local files on the Mac second | preview hit-rate; MAEST/CLAP embeddings, BPM/key, EDM features cached; Discogs400 priors added to evidence; local-file match rate via rekordbox XML/folders | Apple-Silicon install path; preview availability |
| 5 | Personalised genre classifier + Streamlit review | `train` prints GroupKFold accuracy + confusion matrix; `review` UI for lowest-margin/between tracks; final genre playlists; review queue shrinks per round | personal boundaries; tech house vs minimal fuzz |
| 6 | Feel | feel axes + feel categories labelled; `query --genre --feel` playlists sorted by BPM then Camelot distance | preview energy bias (prefer local files) |
| 7 | Maintenance | `sync` runs ingest -> enrich -> assign -> publish for newly liked tracks; optional schedule | — |

Deferred past M3 on purpose: MusicBrainz, Last.fm, ReccoBeats, iTunes, label priors, active learning, Streamlit, pyrekordbox, pandas, pre-commit, `sync`.

## Verification

- **Unit (CI-blocking, no network):** `test_titles` table-driven from `tests/fixtures/titles.csv` (≥ 40 real titles: dash form, parenthesised, bare suffix, `(X Records)` false positive, `feat.`/`ft.`, remixer in artists list, `vs`/`&` joins, Extended/Radio/Dub/Rework/Bootleg/Instrumental); `test_normalize` (alias hit, broad -> parent only, unknown -> None and recorded, Beatport ID beats name); `test_publish_diff` (add/remove/reorder, replace threshold, local-track skipping, 100-URI chunking, idempotence: identical lists -> empty diff); `test_db` (fresh migration, unique constraint rejects duplicate evidence); `test_scorer` (weight caps, remix down-weighting, parent-only voting).
- **Recorded HTTP (vcrpy cassettes, secrets filtered, replayed in CI):** one `/me/tracks` page; `GET /albums/{id}`; SonoVault `resolve` batch (matched / not_found / no_credits); Beatport `tracks/?isrc=` hit and miss + `/catalog/genres/`; Discogs `search?track=`; Deezer `track/isrc:`; negative-cache path (second call never hits transport).
- **Live Spotify smoke test** (`publish --smoke-test`) asserts in order: `POST /me/playlists` -> 201, `public=false`; `POST .../items` with 2 URIs -> `snapshot_id`; `GET .../items` returns exactly those 2 in order; `PUT` replace with 1 URI -> 1; delete succeeds; `GET /me/tracks?limit=1` exposes `external_ids.isrc`; `GET /artists/{id}` logs whether `genres` is non-empty. Fail fast with raw status/body on 403/405.
- **Model (M5):** `GroupKFold` by primary artist; evidence prior computed without user labels; stratified random hold-out separate from the confidently-tagged seed set; per-category precision/recall + confusion matrix; calibration buckets. Target ≥ 80% top-1 on clear categories, with the known-fuzzy pairs reported explicitly.
- **End-to-end:** `publish --dry-run` prints diffs; real publish -> playlists visible in Spotify (and rekordbox); user spot-checks 20 random tracks per category; running `publish` twice makes no changes the second time.

## Remaining user actions before implementation can run end-to-end
1. Open the network allowlist above (or full access) for this environment.
2. Create the Spotify developer app and paste the client ID into `.env` (I will add step-by-step instructions to the README at M0).
3. Have Beatport credentials, a free SonoVault key and a Discogs token ready for M2.
4. Before M4's local-file step: download the tracks into rekordbox or a folder on the Mac and export rekordbox XML.
