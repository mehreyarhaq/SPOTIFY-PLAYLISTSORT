"""Finding audio: rekordbox XML, folder scans, matching, preview downloads."""

from __future__ import annotations

import httpx
import numpy as np
import pytest

from playlistsort import db
from playlistsort.audio.acquire import (
    LocalFile,
    fetch_previews,
    match_local,
    parse_rekordbox_xml,
    register_matches,
    scan_folders,
)
from playlistsort.enrich.base import Http, load_tracks
from playlistsort.enrich.deezer import DeezerEnricher

sf = pytest.importorskip("soundfile")
mutagen = pytest.importorskip("mutagen")

XML = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="7.0.0" Company="AlphaTheta"/>
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="Cola (Franky Rizardo Remix)" Artist="CamelPhat, Elderbrook"
           Album="Cola (Remixes)" TotalTime="412" AverageBpm="124.00" Tonality="Am"
           Location="file://localhost/Users/dj/Music/Cola%20%28Franky%20Rizardo%20Remix%29.aiff"/>
    <TRACK TrackID="2" Name="Rej" Artist="&#194;me" Album="Rej" TotalTime="300.5"
           AverageBpm="122.00" Tonality="Dbm"
           Location="file://localhost/Users/dj/Music/Rej.mp3"/>
  </COLLECTION>
</DJ_PLAYLISTS>
"""


def test_parse_rekordbox_xml(tmp_path) -> None:
    p = tmp_path / "rb.xml"
    p.write_text(XML, encoding="utf-8")
    files = parse_rekordbox_xml(p)
    assert [f.title for f in files] == ["Cola (Franky Rizardo Remix)", "Rej"]
    assert str(files[0].path) == "/Users/dj/Music/Cola (Franky Rizardo Remix).aiff"
    assert files[0].artist == "CamelPhat, Elderbrook"
    assert files[0].duration_s == 412.0 and files[1].duration_s == 300.5
    assert files[0].bpm == 124.0 and files[0].key == "Am"
    assert files[1].artist == "Âme"
    assert all(f.source == "rekordbox" for f in files)


def _wav(path, seconds=2.0, sr=22050):
    y = (0.1 * np.sin(2 * np.pi * 220 * np.arange(int(sr * seconds)) / sr)).astype(np.float32)
    sf.write(path, y, sr)


def test_scan_folders_uses_tags_then_filename(tmp_path) -> None:
    from mutagen.id3 import TIT2, TPE1, TSRC
    from mutagen.wave import WAVE

    tagged = tmp_path / "01 track.wav"
    _wav(tagged, seconds=3.0)
    w = WAVE(str(tagged))
    w.add_tags()
    w.tags.add(TIT2(encoding=3, text="Cola (Franky Rizardo Remix)"))
    w.tags.add(TPE1(encoding=3, text="CamelPhat"))
    w.tags.add(TSRC(encoding=3, text="GB1"))
    w.save()
    untagged = tmp_path / "sub" / "Âme - Rej.wav"
    untagged.parent.mkdir()
    _wav(untagged, seconds=2.0)
    (tmp_path / "notes.txt").write_text("ignore me")

    files = sorted(scan_folders([tmp_path]), key=lambda f: str(f.path))
    assert len(files) == 2
    by_title = {f.title: f for f in files}
    assert by_title["Cola (Franky Rizardo Remix)"].isrc == "GB1"
    assert by_title["Cola (Franky Rizardo Remix)"].artist == "CamelPhat"
    assert by_title["Cola (Franky Rizardo Remix)"].duration_s == pytest.approx(3.0, abs=0.05)
    assert by_title["Rej"].artist == "Âme" and by_title["Rej"].source == "folder"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    with c:
        db.upsert_track(
            c,
            spotify_id="t1",
            title="Cola - Franky Rizardo Remix",
            isrc="GB1",
            base_title="Cola",
            mix_name="Franky Rizardo Remix",
            is_remix=True,
            remixers=("Franky Rizardo",),
            duration_ms=412000,
        )
        db.upsert_track(
            c, spotify_id="t2", title="Rej", isrc="GB2", base_title="Rej", duration_ms=300500
        )
        db.upsert_track(
            c,
            spotify_id="t3",
            title="Nothing Like It",
            isrc="GB3",
            base_title="Nothing Like It",
            duration_ms=200000,
        )
        db.upsert_artist(c, spotify_id="a1", name="CamelPhat")
        db.upsert_artist(c, spotify_id="a2", name="Âme")
        db.link_track_artist(c, track_id="t1", artist_id="a1")
        db.link_track_artist(c, track_id="t2", artist_id="a2")
    return c


def test_match_local_prefers_isrc_then_fuzzy_and_registers_sources(conn, tmp_path) -> None:
    files = [
        LocalFile(
            path=tmp_path / "a.wav",
            title="Cola (Original Mix)",
            artist="CamelPhat",
            album=None,
            duration_s=380.0,
            isrc=None,
            bpm=None,
            key=None,
            source="folder",
        ),
        LocalFile(
            path=tmp_path / "b.wav",
            title="Cola (Franky Rizardo Remix)",
            artist="CamelPhat",
            album=None,
            duration_s=412.0,
            isrc="GB1",
            bpm=None,
            key=None,
            source="folder",
        ),
        LocalFile(
            path=tmp_path / "c.wav",
            title="Rej",
            artist="Âme",
            album=None,
            duration_s=300.0,
            isrc=None,
            bpm=None,
            key=None,
            source="rekordbox",
        ),
    ]
    matches = match_local(load_tracks(conn), files)
    by_track = {m.track_id: m for m in matches}
    assert by_track["t1"].method == "isrc" and by_track["t1"].file.path.name == "b.wav"
    assert (
        by_track["t2"].method in ("fuzzy", "fuzzy_ambiguous")
        and by_track["t2"].file.path.name == "c.wav"
    )
    assert "t3" not in by_track
    n = register_matches(conn, matches)
    assert n == 2
    assert db.best_audio_source(conn, "t1").kind == "local"
    assert db.best_audio_source(conn, "t1").local_path.endswith("b.wav")


def test_fetch_previews_downloads_and_registers(conn, tmp_path) -> None:
    with conn:
        db.add_evidence(conn, db.Evidence("t1", "deezer", "preview_available", "true"))
        db.add_evidence(conn, db.Evidence("t2", "deezer", "preview_available", "false"))
    api_calls = []

    def api(request: httpx.Request) -> httpx.Response:
        api_calls.append(request.url.path)
        return httpx.Response(
            200, json={"id": 1, "isrc": "GB1", "preview": "https://cdn.test/p.mp3?sig=1"}
        )

    def cdn(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "cdn.test"
        return httpx.Response(200, content=b"ID3fakebytes", headers={"content-type": "audio/mpeg"})

    deezer = DeezerEnricher(
        Http(
            conn,
            source="deezer",
            base_url="https://api.deezer.test",
            transport=httpx.MockTransport(api),
            sleep=lambda s: None,
        )
    )
    stats = fetch_previews(
        conn,
        deezer,
        tmp_path / "audio",
        downloader=httpx.Client(transport=httpx.MockTransport(cdn)),
    )
    assert stats.downloaded == 1 and stats.skipped_no_preview == 0
    assert api_calls == ["/track/isrc:GB1"]  # only tracks flagged preview_available
    src = db.best_audio_source(conn, "t1")
    assert src.kind == "deezer_preview" and src.local_path.endswith("t1.mp3")
    assert (tmp_path / "audio" / "t1.mp3").read_bytes() == b"ID3fakebytes"
    # second run: already have it
    stats2 = fetch_previews(
        conn,
        deezer,
        tmp_path / "audio",
        downloader=httpx.Client(transport=httpx.MockTransport(cdn)),
    )
    assert stats2.downloaded == 0 and stats2.already == 1
