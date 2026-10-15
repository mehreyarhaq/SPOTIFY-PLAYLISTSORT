# R2 — Track-level EDM genre sources and the canonical taxonomy (2026-10-15)

## Beatport (backbone)
- Verified on beatport.com (2026-10-14): every track row, including each remix / edit, has its own
  genre, sub-genre, BPM and key ("Going Back — Flash Atkins Remix, Afro House, 105 BPM, B Minor").
- Live genre list (38 electronic + 9 open-format). Sub-genres seen live: Afro House → Afro / Latin,
  3Step, Afro Melodic; Tech House → Latin Tech; Melodic House & Techno → Melodic House, Melodic
  Techno; Minimal / Deep Tech → Bounce, Deep Tech, Minimal House. **There is no "Afro Tech".**
  Other sub-genres from the 2025-05 delivery dictionary and Beatport's label-support article
  ("Beatport Genres and Sub-Genres"): House → Acid, Soulful; Indie Dance → Dark Disco; Techno
  (Peak Time / Driving) → Peak Time, Driving, Psy-Techno (Jan 2026); Techno (Raw / Deep / Hypnotic)
  → Broken, Deep / Hypnotic, Dub, EBM, Raw; Nu Disco / Disco → Funk / Soul, Italo; Mainstage → Big
  Room, Electro House, Future House, Speed House, Future Rave; UK Garage / Bassline → UK Garage,
  Bassline; Trance (Main Floor) → Progressive/Tech/Uplifting/Vocal/Hard; Psy-Trance → Full-On,
  Progressive Psy, Psychedelic, Dark & Forest, Goa, Psycore & Hi-Tech.
- Changes 2024–2026: Ambient / Experimental added and Downtempo split from Organic House (Apr 2025);
  six open-format genres (Sep 2025); Latin Electronic (Apr 2026); Indie Tech split from Indie Dance
  with back-catalogue reclassification (Oct 2026). Re-pull `/v4/catalog/genres/` on each run.
- Labels choose the genre; Beatport may recategorise; mislabels exist.

## Beatport access
- Official v4 API (`api.beatport.com/v4`): OAuth 2, docs auth-gated, partner-portal approval,
  no public client-credentials tier. Apply in parallel.
- Working community routes (2026): (a) log in with your own Beatport account via
  `account.beatport.com/identity/v1/login/` using the public client ID of Beatport's docs app,
  then `/o/authorize/` → `/o/token/` (~10-minute tokens with refresh) — `beets-beatport4` 1.3.0
  (2026-09-18) and fomoplayer; (b) paste a bearer token from the browser (idanbeck `beatport-skill`,
  verified 2026-08-27: clean JSON, no Cloudflare challenge on the API host).
- Endpoints: `GET /v4/catalog/tracks/?isrc=` (verified 2026-08-14), `/catalog/tracks/?id=1,2,3`,
  `/catalog/search/?q=&type=tracks`, `/catalog/genres/` (returns `sub_genres`),
  `/catalog/releases/{id}/tracks`. Track objects: `genre`, `sub_genre`, `bpm`, `key{name,camelot}`,
  `isrc`, `mix_name`, `remixers`, `release.label`.
- No published rate limit; 429s happen; clients space requests 0.5–2 s with backoff.
- `www.beatport.com` HTML (incl. `__NEXT_DATA__`) is behind a Cloudflare managed challenge: scraping
  is dead. Beatport's T&Cs describe the API as for pre-approved licensees (secondary source).

## SonoVault (Rekordcloud, makers of Lexicon)
REST; free key 1,000 req/month, 20/min; `byIsrc`, artist+title search, `resolve` (100 rows per
request) by `spotify_id` / `beatport_id` / ISRC returning `genre[]`, `subgenre[]`, label and
Beatport/Discogs IDs. Aggregates MusicBrainz, Discogs, Spotify, Apple, Tidal, Beatport. Taxonomy
quality unverified — test on 50 known tracks; check whether credits are per item or per request.

## Discogs
Free token; 60 req/min authenticated (25 anonymous), identifying `User-Agent` mandatory.
`GET /database/search?q=&artist=&release_title=&track=&label=&style=` (use `track=` to pin a remix);
`/releases/{id}` → release-level `genres`, `styles` and per-track `tracklist`. Styles are
release-level only. Relevant styles (2026-08 dump, CC0): House, Deep House, Tech House, Minimal,
Minimal Techno, Microhouse, Progressive House, Tribal, Tribal House, Electro House, Hard Techno,
Deep Techno, Dub Techno, Acid House, Garage House, UK Garage, Speed Garage, Bassline, UK Funky,
Breakbeat, Breaks, Broken Beat, Downtempo, Leftfield, Balearic, Nu-Disco, Italo-Disco, Disco,
Electro, Techno, Trance, Progressive Trance, Psy-Trance, Tropical House, Future House, Amapiano,
Gqom. Missing: Afro House, Afro Tech, Melodic Techno/House, Organic House, Indie Dance, Deep Tech.

## Other sources
| Source | Granularity | Access |
|---|---|---|
| Deezer | album-level, coarse; 30 s preview; `bpm` often 0 | no key; `track/isrc:{ISRC}` (colon form); ~50 req/5 s |
| iTunes / Apple Music | coarse store genres; 30 s preview | free; ISRC lookup dead, use `search?term=` |
| MusicBrainz | per-recording genre tags from a curated list (tech house, deep house, minimal, melodic techno, 3-step…) but sparse for underground EDM | free, 1 req/s |
| Last.fm `track.getTopTags` | crowd tags with counts, noisy | api key |
| Traxsource | DJ-store genres incl. Afro House, Soulful, Jackin, Melodic / Progressive House | no API |
| Bandcamp | free-text tags | no official API (`beetcamp`) |
| SoundCloud | user tags | app registration needs Artist Pro |
| 1001Tracklists | which DJs played a track (scene/feel signal) | scrapers only |
| Juno Download | closed June 2026 | — |
| Every Noise at Once | Spotify artist-genre map, frozen Dec 2023 | archive dumps |

## Commercial tagging
Cyanite: V7 `advancedSubgenre` has techHouse/deepHouse/minimal/techno but no afroHouse/melodic;
accepts Spotify IDs; free = 5 analyses/month; API €290/month + usage → skip. Bridge.audio, AIMS,
TrackTag ($0.10/track), choon.app exist. Musiio is gone. Replicate hosts `mtg/effnet-discogs` at
≈$0.00022/run (≈$0.44 for 2,000 tracks). Lexicon's "Find Tags" uses SonoVault and its forum calls
the genres too broad; Mixed In Key gives key + energy 1–10, no genre.

## Labels as a signal
No dataset/API maps labels to sub-genres. Build it: aggregate Beatport genre per `release.label`
from our own enriched tracks (Beatport genre pages also expose a label facet with counts); Discogs
`/labels/{id}/releases` styles as a fallback. Spotify full album objects still expose `label`.

## Reusable crosswalks and code
`metabrainz/genre-matching` (Discogs/Last.fm/tagtraum → MusicBrainz CSVs);
`zabe40/musicbrainz-userscripts` (complete Beatport genre|sub-genre → MusicBrainz tag table);
`hastefuI/discogs-dataset-genres-styles` (CC0 style list); `beets-beatport4`, `ccombe/
beatport-collector` (ISRC-first matching, rekordbox XML output); idanbeck `beatport-skill`
(ISRC first, then asymmetric fuzzy artist+title+mix; auto vs needs-review piles);
`Heyian/spotify-genres` (Oct 2026: MusicBrainz + Discogs-EffNet on Deezer previews);
`evgenygurin/dj-music-plugin` (`canonical.py`: Beatport genre + BPM → canonical techno sub-genre).

## Risks
Beatport may close the login route (one pass, cache everything, apply for partner access); Discogs
60/min (~35 min for 2,000 lookups); SonoVault 1,000/month; 10–25% of a DJ's liked songs (white
labels, SoundCloud-only edits) are in no catalogue → audio path and review are mandatory; taxonomy
drift → version the alias table and canonicalise at score time.
