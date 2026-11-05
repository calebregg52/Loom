"""Where Loom keeps its settings, login token and library snapshot."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

REDIRECT_URI = "http://127.0.0.1:8080/callback"


def app_dir() -> Path:
    """Per-user folder for Loom's files (works the same for the EXE and from source)."""
    override = os.environ.get("LOOM_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / "Loom"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Loom"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "loom"
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return app_dir() / "settings.yaml"


def token_cache_path() -> Path:
    return app_dir() / ".spotify_token"


def library_path() -> Path:
    return app_dir() / "library.json"


def _settings() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _save_setting(key: str, value: str) -> None:
    data = _settings()
    data[key] = value.strip()
    settings_path().write_text(yaml.safe_dump(data), encoding="utf-8")


def load_client_id() -> str | None:
    """Return the saved Spotify client ID, also picking up the old secret.yaml from the repo root."""
    value = (_settings().get("SPOTIFY_CLIENT_ID") or "").strip()
    if value:
        return value
    legacy = Path("secret.yaml")
    if legacy.exists():
        data = yaml.safe_load(legacy.read_text(encoding="utf-8")) or {}
        value = (data.get("SPOTIFY_CLIENT_ID") or "").strip()
        if value:
            save_client_id(value)
            return value
    return None


def save_client_id(client_id: str) -> None:
    _save_setting("SPOTIFY_CLIENT_ID", client_id)


def load_lastfm_key() -> str | None:
    """Optional Last.fm API key, used to fill in genres Spotify doesn't have."""
    return (_settings().get("LASTFM_API_KEY") or "").strip() or None


def save_lastfm_key(key: str) -> None:
    _save_setting("LASTFM_API_KEY", key)


def enrich_cache_path() -> Path:
    return app_dir() / "song_data_cache.json"


def forget_login() -> None:
    token_cache_path().unlink(missing_ok=True)
