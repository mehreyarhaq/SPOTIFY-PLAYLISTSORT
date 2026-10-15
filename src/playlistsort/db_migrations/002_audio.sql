-- 002: audio sources, signal features and embeddings (Milestone 4).

-- Where we have audio for a track. Only local paths, never signed/expiring URLs.
create table if not exists audio_sources (
    track_id    text not null references tracks(spotify_id) on delete cascade,
    kind        text not null,            -- local | deezer_preview | itunes_preview
    local_path  text not null,
    duration_s  real,
    sample_rate integer,
    fetched_at  text not null,
    primary key (track_id, kind)
);

create table if not exists features (
    track_id             text primary key references tracks(spotify_id) on delete cascade,
    input_kind           text not null,
    bpm                  real,
    bpm_raw              real,
    bpm_confidence       real,
    key_tonic            text,
    key_mode             text,
    camelot              text,
    key_strength         real,
    rms_db               real,
    peak_db              real,
    crest_db             real,
    sub_bass_ratio       real,
    hp_ratio             real,
    tempogram_peak       real,
    pump                 real,
    spectral_centroid_hz real,
    duration_s           real,
    json                 text,
    analysed_at          text not null
);

-- Preview and full-file embeddings are never mixed silently: input_kind is part of the key.
create table if not exists embeddings (
    track_id      text not null references tracks(spotify_id) on delete cascade,
    model         text not null,
    model_version text not null,
    input_kind    text not null,
    offset_s      real not null default 0,
    dim           integer not null,
    dtype         text not null,
    blob          blob not null,
    created_at    text not null,
    primary key (track_id, model, model_version, input_kind)
);
