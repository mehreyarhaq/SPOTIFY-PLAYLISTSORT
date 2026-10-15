# R1 — Spotify Web API for a personal Development-Mode app (as of 2026-10-15)

## Still available
- `GET /me/tracks` (scope `user-library-read`), `limit` 1–50, `offset`, `market`. Track objects
  keep `external_ids.isrc` (its removal, announced Feb 2026, was reverted in the March 2026
  changelog), `artists`, `album`, `duration_ms`, `explicit`, `is_local`. Removed: `popularity`,
  `available_markets`, `linked_from`; `preview_url` is null for apps created after 2024-11-27.
  Sources: developer.spotify.com changelog Feb 2026 and Mar 2026; reference page for
  get-users-saved-tracks.
- `GET /albums/{id}` still exposes `label`. Bulk "Get Several ..." endpoints are gone for Dev Mode
  (Feb 2026 changelog) — one artist/album per request, so cache.
- `GET /artists/{id}`: `genres` is badged Deprecated; since ~Jan 2025 many artists return `[]`
  (community thread "Genres from artists have been removed", unresolved Dec 2025). Treat as weak.
- Playlists: `POST /me/playlists` (the `/users/{id}/playlists` form was removed for Dev Mode),
  `POST/PUT/DELETE /playlists/{id}/items` (renamed from `/tracks`; 100 URIs per call),
  `GET /playlists/{id}/items` for own playlists. Scopes `playlist-modify-private` / `-public`.
  ~10,000 tracks per playlist (community), no documented playlist-count cap, folders not accessible.
  Two 2026 community reports of 403 / 405 on create/add in Dev Mode are unconfirmed — smoke test first.
- `/search` limit max 10 in Dev Mode.

## Gone for new apps (2024-11-27 blog post), nothing restored since
Audio Features, Audio Analysis, Recommendations, Related Artists, Featured/Category playlists,
30-second previews, algorithmic/editorial playlists.

## Development Mode rules (Feb–Jul 2026)
- Feb 6 2026 blog "Update on developer access and platform security": app owner must be Premium,
  5 users, 1 client ID (raised to 25 on 2026-07-23, quota per developer account), smaller endpoint
  set for new apps from Feb 11; endpoint changes for *existing* apps postponed indefinitely (Mar 9
  update). Extended quota is organisations-only (≥250k MAU) since May 2025.
- Rate limits: rolling 30 s window, `Retry-After` on 429; also `QUOTA_EXCEEDED` 429 bodies.
- Redirect URIs: loopback `http://127.0.0.1:PORT` (not `localhost`); HTTPS otherwise; PKCE for
  public clients (blog 2025-02-12, enforced for all apps by Nov 2025).

## Terms of service to design around
Developer Policy forbids ingesting Spotify content into ML/AI models and long-term caching beyond
need; scraping is prohibited by the User Guidelines (Sept 2025 update, secondary source).
Design response: Spotify data is used for identity (ISRC, titles, artists, labels) and for writing
playlists only.

## Replacements
- Previews: Deezer `GET https://api.deezer.com/track/isrc:{ISRC}` -> `preview` (30 s MP3, signed URL;
  sometimes empty). iTunes Search `previewUrl` as last resort (promotional-use terms; `lookup?isrc=`
  is dead in 2026 — three independent repos confirm `resultCount: 0`). Spotify embed scraping
  (`open.spotify.com/embed/track/{id}`) works but violates ToS — not used.
- Audio features: ReccoBeats `https://api.reccobeats.com/v1/track/{spotify_id}/audio-features`
  (free, no key, coverage inconsistent, limits unpublished). Cyanite accepts Spotify IDs but is a
  €290/month B2B API. Primary plan: compute features locally (see R3).
- Python clients: spotipy 2.26.0 (2026-03-03) migrated to `/items`, `/me/library`, `POST
  /me/playlists`; `SpotifyPKCE` runs a loopback callback server; 2.25.2 fixed an XSS in the
  callback server (upgrade mandatory). tekore 6.1.1 is the alternative.

## DJ software
Newsroom 2025-09-24: Premium users get the full catalogue and their own playlists inside rekordbox,
Serato and djay (desktop) in 51 markets; Dec 2025 rekordbox/djay mobile; Sept 2026 VirtualDJ,
Cross DJ, edjing. Limits (Serato support article): no recording, two decks, no stems, streaming
only. Playlists this tool creates are ordinary user playlists and appear there unchanged.

## Native Liked Songs filters
The Spotify app's genre/mood filters for Liked Songs (≥15 tracks per filter, ≤15 filters) are not
exposed through the API.
