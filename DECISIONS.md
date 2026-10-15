# Decisions

Short records of choices that are easy to second-guess later. Newest first.

## 2026-10-15 — Personalised classifier instead of trusting any single tag source
Research (EPFL 2026 "acoustic overspecification", Hsu 2022, HouseX-v2 2025) shows tech house vs
minimal and progressive vs melodic overlap acoustically and that even Beatport's own labels are
fuzzy at those borders. No off-the-shelf audio model knows afro house, melodic techno, indie dance
or organic house. Therefore: public tags and audio features are *inputs*; the user's own soft
labels (~20 per playlist is enough per Ma & Lerch 2024) define the output classes.

## 2026-10-15 — Beatport taxonomy is the canonical vocabulary
It is the only source that labels every recording (each remix separately) with a DJ-oriented
genre and sub-genre, plus BPM and key, and it already contains the user's categories. Other
sources map into it via `config/taxonomy.yaml` aliases. Canonicalisation happens at score time
because Beatport adds/renames genres several times a year.

## 2026-10-15 — No Spotify audio features, no Spotify previews
Removed for new apps on 2024-11-27 and never restored. Previews come from Deezer by ISRC (or the
user's local files); audio features are computed locally. Scraping Spotify's embed page works but
violates the Developer Policy, so it is not used.

## 2026-10-15 — Spotify is identity + output only
Spotify's Developer Policy forbids ingesting Spotify content into ML models. The classifier is
trained on user labels, third-party metadata and audio features from non-Spotify sources.

## 2026-10-15 — PyTorch audio path is primary; Essentia optional
`essentia-tensorflow` has no Windows wheels and its Apple Silicon wheel is reported broken; the
user's machine is an Apple Silicon Mac. MAEST (via `transformers`) provides the same Discogs
style head and better embeddings; CLAP is available through `transformers` too.

## 2026-10-15 — Cyanite ruled out, SonoVault in
Cyanite's API costs €290/month and lacks afro/melodic categories. SonoVault (Rekordcloud) offers
1000 free requests/month and resolves Spotify IDs to genre, sub-genre, label and Beatport IDs,
which turns Beatport search into exact lookups.

## 2026-10-15 — Playlists table is the source of truth for idempotent publishing
Description markers get truncated or edited. The `playlists` table maps `(axis, category)` to a
Spotify playlist id; publish verifies the playlist still exists, recreates if deleted, and diffs.

## 2026-10-15 — "playlist", not "crate"
The user did not know the DJ slang. User-facing text uses "playlist" everywhere.
