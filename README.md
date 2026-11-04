# Loom

A desktop app that reads your Spotify library and (soon) weaves it into new playlists: by genre, and as
"journeys" that move gradually through tempo and genre.

## Get the app

Download `Loom.exe` from the latest **Build Windows EXE** run on the
[Actions tab](../../actions/workflows/build-exe.yml) (open a run, then grab the `Loom-windows` artifact).

## Connect Spotify (one time)

1. Go to the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and create an app.
2. Add `http://127.0.0.1:8080/callback` as a **Redirect URI** and tick **Web API**.
3. Open Loom, click **Connect Spotify**, and paste the app's **Client ID**. No client secret is needed.
4. Your browser opens to approve access; after that Loom syncs your playlists and liked songs.

Loom keeps its settings, login token and a `library.json` snapshot in `%APPDATA%\Loom`.

## Run from source

```
pip install -r requirements.txt
python main.py
```

Build the EXE yourself with `pip install pyinstaller` then `pyinstaller loom.spec` (output in `dist/`).
Run tests with `python -m pytest`.

## Song data

Each sync also adds genres and audio features to your songs:

- **Genres** come from Spotify's artist data. Spotify has been thinning this out, so if you add a free
  [Last.fm API key](https://www.last.fm/api/account/create) in **Settings**, Loom fills in
  missing genres from Last.fm tags.
- **BPM, energy, mood, danceability and key** come from [ReccoBeats](https://reccobeats.com), a free API
  that looks songs up by Spotify ID (Spotify no longer gives this data to new apps). Songs it doesn't know show blank.

Results are cached in `%APPDATA%\Loom\song_data_cache.json`, so later syncs only look up new songs.
