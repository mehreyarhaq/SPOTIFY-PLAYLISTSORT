-- 001: core library schema (Milestones 1-3). Audio tables arrive in 002.

create table if not exists schema_version (
    version    integer primary key,
    applied_at text not null
);

create table if not exists tracks (
    spotify_id       text primary key,
    isrc             text,
    title            text not null,
    base_title       text,
    mix_name         text,
    mix_type         text,
    remixers_json    text,
    is_remix         integer not null default 0,
    title_confidence real,
    album_id         text,
    duration_ms      integer,
    explicit         integer not null default 0,
    is_local         integer not null default 0,
    added_at         text,
    raw_json         text,
    updated_at       text
);
create index if not exists idx_tracks_isrc on tracks(isrc);
create index if not exists idx_tracks_base_title on tracks(base_title);

create table if not exists artists (
    spotify_id  text primary key,
    name        text not null,
    genres_json text,
    fetched_at  text
);

create table if not exists track_artists (
    track_id  text not null references tracks(spotify_id) on delete cascade,
    artist_id text not null references artists(spotify_id) on delete cascade,
    position  integer not null default 0,
    primary key (track_id, artist_id)
);

create table if not exists albums (
    spotify_id   text primary key,
    name         text,
    label        text,
    release_date text,
    fetched_at   text
);

-- One raw tag from one source for one track. Canonicalisation happens at score time.
create table if not exists evidence (
    id            integer primary key autoincrement,
    track_id      text not null references tracks(spotify_id) on delete cascade,
    source        text not null,
    field         text not null,
    raw_value     text not null,
    source_tag_id text,
    match_method  text not null default 'isrc',
    match_score   real not null default 1.0,
    weight        real not null default 1.0,
    meta_json     text,
    fetched_at    text,
    unique (track_id, source, field, raw_value)
);
create index if not exists idx_evidence_source on evidence(source);

create table if not exists taxonomy_snapshots (
    source     text not null,
    version    text not null,
    fetched_at text not null,
    json       text not null,
    primary key (source, version)
);

-- Cache of external calls, including negative results. Never store signed/expiring URLs here.
create table if not exists http_cache (
    source      text not null,
    endpoint    text not null,
    params_hash text not null,
    status      integer not null,
    body        text,
    fetched_at  text not null,
    expires_at  text not null,
    primary key (source, endpoint, params_hash)
);

-- User labels are append-only; the latest session per (track, axis) wins.
create table if not exists labels (
    id         integer primary key autoincrement,
    session_id text not null,
    track_id   text not null references tracks(spotify_id) on delete cascade,
    axis       text not null,
    category   text not null,
    weight     real not null default 1.0,
    kind       text not null default 'seed',
    note       text,
    created_at text not null
);
create index if not exists idx_labels_track_axis on labels(track_id, axis);

create table if not exists assignments (
    run_id   text not null,
    track_id text not null references tracks(spotify_id) on delete cascade,
    axis     text not null,
    category text not null,
    prob     real not null,
    primary key (run_id, track_id, axis, category)
);

-- Source of truth for idempotent publishing: which Spotify playlist holds which category.
create table if not exists playlists (
    axis        text not null,
    category    text not null,
    spotify_id  text not null,
    name        text not null,
    snapshot_id text,
    synced_at   text,
    primary key (axis, category)
);
