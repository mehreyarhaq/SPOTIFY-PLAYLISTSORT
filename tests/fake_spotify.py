"""An in-memory stand-in for the handful of spotipy methods the publisher uses."""

from __future__ import annotations

import itertools

from spotipy.exceptions import SpotifyException


class FakeSpotify:
    def __init__(self) -> None:
        self.playlists: dict[str, dict] = {}
        self.items: dict[str, list[str]] = {}
        self.calls: list[tuple] = []
        self._ids = itertools.count(1)

    def me(self):
        return {"id": "dj", "display_name": "DJ", "product": "premium", "country": "GB"}

    def user_playlist_create(self, user, name, public=True, collaborative=False, description=""):
        pid = f"pl{next(self._ids)}"
        self.playlists[pid] = {
            "id": pid,
            "name": name,
            "public": public,
            "description": description,
        }
        self.items[pid] = []
        self.calls.append(("create", pid, name))
        return self.playlists[pid]

    def playlist(self, playlist_id, fields=None, market=None, additional_types=("track",)):
        if playlist_id not in self.playlists:
            raise SpotifyException(404, -1, "not found")
        return self.playlists[playlist_id]

    def playlist_items(
        self,
        playlist_id,
        fields=None,
        limit=100,
        offset=0,
        market=None,
        additional_types=("track",),
    ):
        uris = self.items[playlist_id][offset : offset + limit]
        nxt = "more" if offset + limit < len(self.items[playlist_id]) else None
        return {"items": [{"item": {"uri": u}} for u in uris], "next": nxt}

    def playlist_replace_items(self, playlist_id, items):
        self.items[playlist_id] = list(items)
        self.calls.append(("replace", playlist_id, len(items)))
        return {"snapshot_id": "s"}

    def playlist_add_items(self, playlist_id, items, position=None):
        self.items[playlist_id].extend(items)
        self.calls.append(("add", playlist_id, len(items)))
        return {"snapshot_id": "s"}

    def playlist_remove_all_occurrences_of_items(self, playlist_id, items, snapshot_id=None):
        self.items[playlist_id] = [u for u in self.items[playlist_id] if u not in set(items)]
        self.calls.append(("remove", playlist_id, len(items)))
        return {"snapshot_id": "s"}

    def current_user_unfollow_playlist(self, playlist_id):
        self.playlists.pop(playlist_id, None)
        self.items.pop(playlist_id, None)
        self.calls.append(("unfollow", playlist_id))
