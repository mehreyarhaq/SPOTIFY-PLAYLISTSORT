# R3 — Audio analysis for fine-grained EDM subgenre and "feel" (2026-10-15)

## Off-the-shelf genre heads
- Essentia `genre_discogs400` (Discogs-EffNet) — 106 Electronic styles incl. Deep House, Deep
  Techno, Dub Techno, Electro House, Garage House, Hard House, House, Italo-Disco, Leftfield,
  Minimal, Minimal Techno, Nu-Disco, Progressive House, Progressive Trance, Tech House, Techno,
  Tribal, Tribal House, UK Garage, Downtempo. MAEST's 519-style head adds Footwork, UK Funky,
  Neo Trance etc. **Missing everywhere:** Afro House, Afro Tech, Melodic House & Techno, Indie
  Dance, Organic House, Peak-time vs Raw. → pretrained models give embeddings and priors; the final
  head is trained on the user's labels.
- MAEST (MTG, ISMIR 2023, arXiv:2309.16418): HF `mtg-upf/discogs-maest-30s-pw-129e`, 86.9M params,
  CC BY-NC-SA, `trust_remote_code=True`; 30 s input best; beats EffNet-B0 on every downstream task;
  2–3× CNN throughput on CPU with inference patchout; training data over-represents electronic music.
- Essentia-TensorFlow install reality (PyPI, 2026-10-15): `2.1b6.dev1389` (2025-07) has wheels for
  cp39–cp313 (manylinux x86_64, macOS 14 x86_64, macOS 15 arm64); `dev1438` (2026-05) is cp314
  only. **No Windows wheels ever** (issues #1340, #1495). A 2026 repo reports the arm64 wheel's TF
  algorithms fail to import and documents a source build. → PyTorch path primary; Essentia optional.

## Embeddings for similarity / zero-shot
| Model | Notes |
|---|---|
| LAION CLAP music (512-d; available via `transformers` ClapModel) | best raw-cosine kNN geometry among 6 encoders in a 2026 study (arXiv:2608.06928); zero-shot prompts fragile for EDM subgenres (arXiv:2411.12209) |
| MERT-v1-95M/330M (CC-BY-NC) | good linear-probe backbone, poor mean-pooled cosine similarity |
| MuQ / MuQ-MuLan (2025, weights CC-BY-NC) | SOTA on MARBLE (GTZAN 85.6), zero-shot MTT 79.3; heavy |
| Audio LLMs (Qwen2-Audio etc.) | F1 0.03–0.11 on 8 house subgenres vs 0.76 for a small CNN (arXiv:2409.06690) → do not use |

## Published EDM subgenre results
- Caparrini et al. 2020 (Beatport top-100, 23/29 subgenres): 59% / 48%; top-4 features all tempo.
- Hsu, Chen, Yang 2022 (30 Beatport subgenres × 2,500 previews; github.com/mir-aidj/EDM-subgenre-classifier):
  50.4% song-level from a 30 s excerpt vs 55.4% from 120 s; + Fourier/autocorrelation tempograms →
  60.6%. Tech house / deep house / progressive house "fuzzy even for DJs".
- HouseX-v2 2025 (8 house subgenres, expert **soft labels**, drop-only 7.5 s clips; BSD-3 data):
  soft labels beat hard; ResNet152 F1 0.764.
- EPFL "Acoustic overspecification in EDM taxonomy" 2026 (3,500 Beatport tracks, 35 genres; MIT code
  github.com/alunxu/edm-overspec): only 17–20 acoustic families; **Tech House vs Minimal and
  Progressive House vs Melodic House & Techno collapse**; Afro House, Organic House, Peak Techno,
  Psy-Trance, Downtempo form one "melodic & atmospheric" gradient; hand-made EDM features
  (tempogram, sub-bass ratio, sidechain-pumping proxy, H/P ratio, crest factor) cluster better than
  MERT-95M and CLAP-tiny; tempo/tempogram, energy, spectral modulation top feature importance.
- Ma & Lerch 2024 (arXiv:2409.07730): logistic-regression probes on frozen embeddings reach
  near-SOTA with ~20 labelled examples per tag; concatenating heterogeneous embeddings helps.

## "Feel" as DJs use it
Mixed In Key Energy 1–10 (patent US 8,865,993: transient/ambient attribute profile; not tempo);
rekordbox has no energy field; Serato gets "2A – Energy 7" via MIK. Camelot moves (±1, A↔B, +2
energy jump, +7). Recommendation: BPM/key order tracks inside a playlist; feel axes = energy /
intensity, tempo band, dark–bright / melodic, vocal presence, groove vs driving. A 2026 Music
Perception study found Spotify energy/valence tracked human ratings but danceability did not.

## Pipeline evidence
- 30 s previews: usable for genre (drop-centred helps) but bias energy upward and hide intros/outros.
- Clustering vs supervised: generic embeddings alone cluster EDM poorly (bearblog 2025; EPFL 2026);
  use UMAP only for inspection; train a soft-label probe on embeddings ⊕ EDM features ⊕ evidence
  priors; multi-membership; active learning on lowest-margin tracks.
- BPM: DeepRhythm Acc1 95.9% at 0.12 s/track CPU vs Essentia RhythmExtractor 87.9% (vendor table;
  octave errors dominate) → octave-fold into 110–140 for house/techno. Key: Essentia `edma` profiles
  (Faraldo 2016) or librosa fallback.

## Audio acquisition
Local files via `pyrekordbox` 0.5.0 (XML / master.db) and folder scan (`mutagen` tags; fuzzy
title + duration) > Deezer preview by ISRC (`track/isrc:`; signed URL, store the downloaded file
not the URL; ~50 req/5 s; gaps for underground releases) > iTunes `search?term=` previews
(`lookup?isrc=` dead) > never Spotify embed scraping (ToS) or YouTube (ToS).

## Reusable projects
AudioMuse-AI (2.7k★, AGPL; MusiCNN+CLAP clustering), Essentia-to-Metadata (MIT; 5–15 s/track on an
i3), navidrome-mood-plugin, DataJockey (a DJ's 4k library on EffNet embeddings + Streamlit),
reklawdbox (Rust; audio + Discogs/MusicBrainz fusion with confidence capping), housex-v2,
EDM-subgenre-classifier, edm-overspec (feature extractor directly reusable).

## Recommended stack
Primary (all platforms): Python 3.12, `torch`, `transformers` (MAEST + CLAP), `librosa`,
`soundfile`, `deeprhythm`; Essentia extras on Linux x86_64 only. Per track: MAEST layer-7 embedding,
CLAP 512-d, Discogs400 electronic probabilities (priors), BPM (octave-corrected), key/Camelot,
RMS/crest, sub-bass ratio, sidechain-pump proxy, H/P ratio, cyclic-tempogram concentration, plus
optional Essentia heads (danceability, moods, engagement, voice/instrumental, timbre). 2,000 × 30 s
≈ 1–3 h CPU per backbone; cache everything.
