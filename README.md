# playlistsort

Sort a Spotify **Liked Songs** library (~2000 house/techno-family tracks) into private Spotify
playlists that are accurate enough for a DJ to mix from: first by **genre** at Beatport
granularity (Afro House › Afro Melodic, Tech House, Minimal / Deep Tech › Deep Tech, Melodic
House & Techno › Melodic Techno, Indie Dance, ...), later by **feel**, and finally by any
combination of the two.

The approach, in one paragraph: Spotify is used only to read the library and write playlists.
Genre evidence comes from sources that label **each recording** (Beatport first, then
SonoVault, Discogs, Deezer), is normalised into one canonical taxonomy through an alias table,
and is fused with weights into a probability per genre. Audio analysis of the actual recording
(previews or your local files) adds embeddings, BPM, key and EDM-specific features. The final
decision is a small classifier trained on **your own** labels, so the playlists follow your
mixing boundaries rather than any one website's opinion. Tracks that sit between two genres
are reported as such instead of being forced.

See `docs/research/` for the research this design rests on and `DECISIONS.md` for the why.

## Status

| Milestone | State |
|---|---|
| 0 Project setup | done |
| 1 Spotify round-trip (smoke test, ingest) | code + unit tests done; live smoke test pending network access |
| 2 Metadata enrichment (SonoVault → Beatport → Discogs → Deezer) | code + unit tests done; live run pending network access |
| 3 First-pass genre playlists + review CSV | planned |
| 4 Audio features and embeddings | planned |
| 5 Personalised classifier + review UI | planned |
| 6 Feel playlists and combined queries | planned |
| 7 Sync for newly liked tracks | planned |

## Setup

### 1. Python environment

Requires Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
make setup            # uv sync --group dev
uv run playlistsort --help
```

### 2. Spotify developer app (one-time, 2 minutes)

1. Open https://developer.spotify.com/dashboard and create an app.
2. Redirect URI: exactly `http://127.0.0.1:8888/callback` (Spotify no longer accepts `localhost`).
3. API used: **Web API**. The app owner must be a Spotify **Premium** account (Development
   Mode rule since February 2026).
4. Copy the **Client ID** into `.env` (see `.env.example`). No client secret is needed; the tool
   uses the PKCE flow.

Log in:

```bash
uv run playlistsort auth login                 # opens a browser on your machine
uv run playlistsort auth login --paste-redirect  # headless machines: prints a URL, you paste the redirect back
```

The token is stored at `data/tokens/spotify.json`. Copy that file between machines if you log in
on your laptop but run the pipeline somewhere else.

### 3. Smoke test, then ingest

```bash
uv run playlistsort publish --smoke-test   # creates, fills, replaces and deletes a private test playlist
uv run playlistsort ingest                 # reads all Liked Songs into data/library.sqlite
uv run playlistsort status
```

### 4. Keys for Milestone 2

Put these in `.env` when you reach enrichment:

- **Beatport**: your Beatport username and password (the tool logs in once and keeps a refresh
  token), or a bearer token copied from your browser.
- **SonoVault**: free API key from https://sonovault.now (1000 requests/month).
- **Discogs**: personal access token from https://www.discogs.com/settings/developers.

### Running in a Claude Code cloud environment

The default "Limited" network policy blocks every host this tool talks to. In the environment
settings (cloud environment menu in the session title bar → Edit → Network access) either choose
full access or add these to **Allowed domains**, keeping "Allow package managers" ticked:

```
api.spotify.com  accounts.spotify.com
account.beatport.com  api.beatport.com
sonovault.now  api.discogs.com
api.deezer.com  *.dzcdn.net
musicbrainz.org  ws.audioscrobbler.com  api.reccobeats.com
huggingface.co  cdn-lfs.huggingface.co  cdn-lfs-us-1.hf.co
itunes.apple.com  audio-ssl.itunes.apple.com
```

Docs: https://code.claude.com/docs/en/cloud-environments#network-access

## Commands

```
playlistsort auth login [--paste-redirect]   Spotify login (PKCE)
playlistsort auth status                     show who is logged in
playlistsort ingest [--limit N]              Liked Songs -> SQLite
playlistsort enrich [--source X] [--limit N]  look tracks up in SonoVault, Beatport, Discogs, Deezer
playlistsort taxonomy pull                   fetch Beatport's current genre tree, report gaps in config
playlistsort taxonomy unmapped               raw tags with no alias yet
playlistsort publish --smoke-test            verify playlist write access end to end
playlistsort publish [--dry-run]             create/update the managed playlists
playlistsort status [--coverage]             library and evidence coverage report
```

Later milestones add `score`, `labels export|import`, `assign`, `audio`, `review`, `train`,
`query` and `sync`.

## Development

```bash
make check     # ruff + pytest
make fmt       # format and autofix
```

Unit tests never touch the network. HTTP interactions are recorded with vcrpy cassettes under
`tests/cassettes/` and replayed in CI.

## Data and terms of service

- Spotify data is used only to identify tracks (ISRC, titles, artists, labels) and to write
  playlists. It is not fed into any model. Playlists are created **private**.
- Audio comes from your own files or from 30-second previews served by third-party catalogue APIs,
  never by scraping Spotify.
- Beatport's public API is formally for approved partners; this tool uses your own Beatport login,
  keeps request volume to one pass over your library, and caches everything. Apply for partner
  access if you want a supported route.
- All open-source audio models used later (MTG Essentia / MAEST) are CC BY-NC-SA: fine for
  personal use, not for a commercial product.
