"""Spotify login with the PKCE flow.

Two modes:
  * default: spotipy opens a browser and catches the redirect on 127.0.0.1:8888;
  * `--paste-redirect`: for headless machines. We print the authorize URL, the user opens it
    anywhere, logs in, and pastes the resulting redirect URL (or just its `code`) back.
The token lives in `data/tokens/spotify.json` and can be copied between machines.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyPKCE

from playlistsort.config import SPOTIFY_SCOPES, Settings


class AuthError(RuntimeError):
    pass


def code_from_redirect(value: str) -> str:
    """Extract the authorization code from a pasted redirect URL (or accept a bare code)."""
    text = (value or "").strip()
    if not text:
        raise AuthError("Nothing was pasted.")
    if text.startswith("http") or "?" in text:
        query = parse_qs(urlparse(text).query)
        if "error" in query:
            raise AuthError(f"Spotify returned an error: {query['error'][0]}")
        if "code" not in query:
            raise AuthError("The pasted URL has no 'code' parameter.")
        return query["code"][0]
    return text


def build_pkce(settings: Settings, *, open_browser: bool) -> SpotifyPKCE:
    if not settings.spotify_client_id:
        raise AuthError("SPOTIFY_CLIENT_ID is not set. Copy .env.example to .env and fill it in.")
    settings.token_path.parent.mkdir(parents=True, exist_ok=True)
    return SpotifyPKCE(
        client_id=settings.spotify_client_id,
        redirect_uri=settings.spotify_redirect_uri,
        scope=SPOTIFY_SCOPES,
        cache_handler=CacheFileHandler(cache_path=str(settings.token_path)),
        open_browser=open_browser,
    )


def login(
    settings: Settings,
    *,
    paste_redirect: bool,
    prompt: Callable[[str], str] = input,
    echo: Callable[[str], None] = print,
) -> None:
    pkce = build_pkce(settings, open_browser=not paste_redirect)
    if paste_redirect:
        echo("Open this URL in a browser on any device, log in to Spotify, approve the app,")
        echo("then paste the FULL URL of the page you land on (it starts with the redirect URI):")
        echo("")
        echo(pkce.get_authorize_url())
        echo("")
        code = code_from_redirect(prompt("Redirect URL: "))
        pkce.get_access_token(code=code, check_cache=False)
    else:
        pkce.get_access_token(check_cache=True)


def client(settings: Settings) -> spotipy.Spotify:
    """A Spotify client using the cached token (refreshing it when needed)."""
    pkce = build_pkce(settings, open_browser=False)
    if not pkce.get_cached_token():
        raise AuthError("Not logged in. Run: playlistsort auth login [--paste-redirect]")
    return spotipy.Spotify(
        auth_manager=pkce,
        requests_timeout=30,
        retries=5,
        status_retries=5,
        backoff_factor=0.5,
    )


def whoami(sp: spotipy.Spotify) -> dict:
    me = sp.me()
    return {
        "id": me.get("id"),
        "display_name": me.get("display_name"),
        "product": me.get("product"),
        "country": me.get("country"),
    }
