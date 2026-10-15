"""Settings from `.env` / environment variables plus derived paths."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

SPOTIFY_SCOPES = (
    "user-library-read playlist-read-private playlist-modify-private playlist-modify-public"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    spotify_client_id: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"

    beatport_username: str = ""
    beatport_password: str = ""
    beatport_bearer_token: str = ""
    sonovault_api_key: str = ""
    sonovault_base_url: str = "https://api.sonovault.now/v1"
    discogs_token: str = ""

    local_music_dirs: str = ""
    rekordbox_xml: str = ""

    playlistsort_data_dir: Path = Path("./data")
    playlistsort_config_dir: Path | None = None

    # ------------------------------------------------------------------ derived paths
    @property
    def data_dir(self) -> Path:
        return self.playlistsort_data_dir.expanduser().resolve()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "library.sqlite"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "tokens" / "spotify.json"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def config_dir(self) -> Path:
        if self.playlistsort_config_dir:
            return self.playlistsort_config_dir.expanduser().resolve()
        repo_cfg = REPO_ROOT / "config"
        return repo_cfg if repo_cfg.exists() else Path.cwd() / "config"

    @property
    def taxonomy_path(self) -> Path:
        return self.config_dir / "taxonomy.yaml"

    @property
    def weights_path(self) -> Path:
        return self.config_dir / "weights.yaml"

    def music_dirs(self) -> list[Path]:
        return [Path(p).expanduser() for p in self.local_music_dirs.split(":") if p.strip()]


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    return Settings()
