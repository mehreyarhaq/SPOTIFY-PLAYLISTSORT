"""Build configured enrichers from settings. Order matters: cheap, exact sources first."""

from __future__ import annotations

import sqlite3
from typing import Protocol

from playlistsort.config import Settings
from playlistsort.enrich import beatport, deezer, discogs, sonovault
from playlistsort.enrich.base import EnrichStats, Http

ORDER = ("sonovault", "beatport", "discogs", "deezer")


class ConfigError(RuntimeError):
    pass


class Enricher(Protocol):
    name: str

    def enrich(
        self,
        conn: sqlite3.Connection,
        *,
        only_missing: bool = ...,
        limit: int | None = ...,
        progress=...,
    ) -> EnrichStats: ...


def build_enricher(name: str, settings: Settings, conn: sqlite3.Connection) -> Enricher:
    if name == "sonovault":
        if not settings.sonovault_api_key:
            raise ConfigError("SONOVAULT_API_KEY is not set (free key at https://sonovault.now).")
        http = Http(
            conn,
            source="sonovault",
            base_url=settings.sonovault_base_url,
            headers={"x-api-key": settings.sonovault_api_key},
            min_interval=3.1,  # 20 requests / minute
        )
        return sonovault.SonoVaultEnricher(http)
    if name == "beatport":
        auth = beatport.BeatportAuth(
            bearer_token=settings.beatport_bearer_token or None,
            username=settings.beatport_username or None,
            password=settings.beatport_password or None,
            token_path=settings.data_dir / "tokens" / "beatport.json",
        )
        http = Http(
            conn, source="beatport", base_url=beatport.API_BASE, min_interval=beatport.MIN_INTERVAL
        )
        try:
            return beatport.BeatportEnricher(http, auth)
        except beatport.BeatportAuthError as exc:
            raise ConfigError(str(exc)) from exc
    if name == "discogs":
        token = settings.discogs_token or None
        http = Http(
            conn,
            source="discogs",
            base_url=discogs.BASE_URL,
            min_interval=discogs.MIN_INTERVAL if token else 2.5,
        )
        return discogs.DiscogsEnricher(http, token=token)
    if name == "deezer":
        http = Http(
            conn, source="deezer", base_url=deezer.BASE_URL, min_interval=deezer.MIN_INTERVAL
        )
        return deezer.DeezerEnricher(http)
    raise ConfigError(f"Unknown source {name!r}. Choose from: {', '.join(ORDER)}, all.")
