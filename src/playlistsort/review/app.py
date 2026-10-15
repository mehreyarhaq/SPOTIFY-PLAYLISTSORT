"""Streamlit review UI: listen, see the evidence, label. Run with `playlistsort review --ui`.

Deliberately small: one uncertain track at a time, the top candidate genres as buttons, plus
"between", "not", a free-text genre and skip. Every click appends a label row in the current
session; later sessions override earlier ones per track.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from playlistsort import db
from playlistsort.config import load_settings
from playlistsort.labels import resolve_category
from playlistsort.model.scorer import load_weights, score_library
from playlistsort.taxonomy.normalize import load_taxonomy

AXIS = "genre"


@st.cache_resource
def _setup():
    settings = load_settings()
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    tax = load_taxonomy(settings.taxonomy_path)
    weights = load_weights(settings.weights_path)
    return settings, conn, tax, weights


def _queue(conn, tax, weights, include_ok: bool):
    scores = score_library(conn, taxonomy=tax, weights=weights)
    labelled = set(db.latest_labels(conn, axis=AXIS))
    order = {"between": 0, "unknown": 1, "ok": 2}
    items = [
        s
        for s in scores.values()
        if s.track_id not in labelled and (include_ok or s.status != "ok")
    ]
    items.sort(key=lambda s: (order.get(s.status, 9), s.margin))
    return items, len(labelled)


def _track_info(conn, tid: str):
    row = conn.execute(
        """
        select t.title, a.label, a.name as album,
               (select group_concat(ar.name, ', ') from track_artists ta join artists ar
                  on ar.spotify_id = ta.artist_id where ta.track_id = t.spotify_id) as artists
        from tracks t left join albums a on a.spotify_id = t.album_id where t.spotify_id = ?
        """,
        (tid,),
    ).fetchone()
    evidence = conn.execute(
        "select source, field, raw_value from evidence where track_id=? and field in"
        " ('genre','subgenre','style','tag','bpm','key','label') order by source, field",
        (tid,),
    ).fetchall()
    return row, evidence


def _label(conn, session_id: str, tid: str, labels: list[tuple[str, float, str]]) -> None:
    with conn:
        for cat, w, kind in labels:
            db.add_label(
                conn,
                session_id=session_id,
                track_id=tid,
                axis=AXIS,
                category=cat,
                weight=w,
                kind=kind,
            )
    st.session_state["idx"] = st.session_state.get("idx", 0) + 1
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="playlistsort review", layout="wide")
    settings, conn, tax, weights = _setup()
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    session_id = st.session_state["session_id"]

    st.sidebar.title("playlistsort review")
    include_ok = st.sidebar.checkbox("Also review confident tracks", value=False)
    queue, n_labelled = _queue(conn, tax, weights, include_ok)
    idx = st.session_state.get("idx", 0)
    st.sidebar.write(f"Session `{session_id}`")
    st.sidebar.write(f"{n_labelled} tracks labelled, {len(queue)} in the queue")
    if st.sidebar.button("Skip to start"):
        st.session_state["idx"] = 0
        st.rerun()
    if idx >= len(queue):
        st.success("Queue done. Run `playlistsort train` then `playlistsort assign` and `publish`.")
        return

    s = queue[idx]
    row, evidence = _track_info(conn, s.track_id)
    st.header(row["title"] if row else s.track_id)
    st.caption(
        f"{row['artists'] or ''}  ·  {row['album'] or ''}  ·  {row['label'] or ''}" if row else ""
    )
    cols = st.columns([2, 1])
    with cols[0]:
        src = db.best_audio_source(conn, s.track_id)
        if src and Path(src.local_path).exists():
            st.audio(str(src.local_path))
            st.caption(f"audio: {src.kind}")
        st.markdown(f"[Open in Spotify](https://open.spotify.com/track/{s.track_id})")
        st.write(f"Status: **{s.status}**  ·  margin {s.margin:.2f}")
        if evidence:
            st.table(
                [
                    {"source": e["source"], "field": e["field"], "value": e["raw_value"]}
                    for e in evidence
                ]
            )
    with cols[1]:
        st.subheader("Which playlist?")
        candidates = [tag for tag, _p in s.ranked[:4]]
        for tag, prob in s.ranked[:4]:
            if st.button(
                f"{tax.path_name(tag)}  ({prob:.0%})", key=f"c-{tag}", use_container_width=True
            ):
                _label(conn, session_id, s.track_id, [(tag, 1.0, "seed")])
        if len(candidates) >= 2 and st.button(
            f"Between {tax.path_name(candidates[0])} and {tax.path_name(candidates[1])}",
            use_container_width=True,
        ):
            _label(
                conn,
                session_id,
                s.track_id,
                [(candidates[0], 0.5, "between"), (candidates[1], 0.5, "between")],
            )
        if candidates and st.button(
            f"NOT {tax.path_name(candidates[0])}", use_container_width=True
        ):
            _label(conn, session_id, s.track_id, [(candidates[0], 1.0, "negative")])
        other = st.text_input("Other genre (name, path or alias)", key=f"other-{s.track_id}")
        if other:
            cat = resolve_category(other, tax)
            if cat is None:
                st.warning(f"'{other}' is not in the taxonomy.")
            elif st.button(f"Use {tax.path_name(cat)}", use_container_width=True):
                _label(conn, session_id, s.track_id, [(cat, 1.0, "seed")])
        if st.button("Skip", use_container_width=True):
            st.session_state["idx"] = idx + 1
            st.rerun()


main()
