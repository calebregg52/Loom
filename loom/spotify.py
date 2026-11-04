"""Spotify login. Uses PKCE, so only the app's client ID is needed (no client secret)."""

from __future__ import annotations

import spotipy
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


def make_client(client_id: str) -> spotipy.Spotify:
    auth = SpotifyPKCE(
        client_id=client_id,
        redirect_uri=config.REDIRECT_URI,
        scope=SCOPES,
        cache_path=str(config.token_cache_path()),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth, requests_timeout=20, retries=5)
