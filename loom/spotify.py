"""Spotify login. Uses PKCE, so only the app's client ID is needed (no client secret)."""

from __future__ import annotations

import threading
import time

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyPKCE

from . import config

# Read the library now; the modify scopes are for creating the new playlists later.
SCOPES = " ".join(
    [
        "user-library-read",
        "playlist-read-private",
        "playlist-read-collaborative",
        "playlist-modify-private",
        "playlist-modify-public",
    ]
)

MAX_WAIT = 30  # seconds; a longer rate-limit wait is reported instead of silently sleeping


class RateLimited(Exception):
    def __init__(self, seconds: float):
        super().__init__(f"Spotify asked Loom to wait {_human(seconds)} before more requests")
        self.seconds = seconds


class Cancelled(Exception):
    pass


def _human(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600} hr {seconds % 3600 // 60} min"
    if seconds >= 60:
        return f"{seconds // 60} min"
    return f"{seconds} s"


class LoomSpotify(spotipy.Spotify):
    """spotipy client that never sleeps for long on a rate limit and can be cancelled."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel_event = threading.Event()

    def _internal_call(self, method, url, payload, params):
        for attempt in range(4):
            if self.cancel_event.is_set():
                raise Cancelled()
            try:
                return super()._internal_call(method, url, payload, params)
            except SpotifyException as exc:
                if exc.http_status != 429:
                    raise
                try:
                    wait = float((exc.headers or {}).get("Retry-After", 5))
                except (TypeError, ValueError):
                    wait = 5.0
                if wait > MAX_WAIT or attempt == 3:
                    raise RateLimited(wait) from exc
                if self.cancel_event.wait(wait + 0.5):
                    raise Cancelled()
        raise RateLimited(MAX_WAIT)


def make_client(client_id: str) -> LoomSpotify:
    auth = SpotifyPKCE(
        client_id=client_id,
        redirect_uri=config.REDIRECT_URI,
        scope=SCOPES,
        cache_path=str(config.token_cache_path()),
        open_browser=True,
    )
    # 429 is left out of the automatic retries so a long Retry-After can't freeze the app.
    return LoomSpotify(
        auth_manager=auth, requests_timeout=20, retries=3, status_retries=3,
        status_forcelist=(500, 502, 503, 504),
    )
