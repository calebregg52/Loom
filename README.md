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

- **Gaps:** songs ReccoBeats doesn't know get BPM from [Deezer](https://developers.deezer.com/api) (free, by ISRC).
  If neither has it, Loom estimates from the average of the same artist's other songs. Artists with no genre
  get one guessed from your playlist names ("Slow Country" → country) or from the artists they share playlists
  with. Estimates are shown grey with a `~`.

Results are cached in `%APPDATA%\Loom\song_data_cache.json`, so later syncs only look up new songs.

## Genre playlists

Open **Genre Playlists** in the sidebar. Pick where the songs come from (Liked Songs, all your songs, or one
playlist), group by broad genre (Country, Rock, Hip Hop…) or exact genre (Red Dirt, Texas Country…), and set a
minimum group size. Tick the groups you want and click **Create playlists on Spotify**. Each one becomes a
private playlist named `Loom · <genre>`. Running it again refills those same Loom playlists instead of
making duplicates, and playlists Loom didn't make are never touched.

**Is Last.fm working?** The Overview page has a *Where genres came from* card that counts artists by source and
shows a green, grey or red light for Last.fm. **Settings → Test key** checks a key straight away.

## Journeys

Open **Journeys** in the sidebar. Pick a source, an optional genre, a shape (**Wind down**, **Build up**,
**Arc** or **Steady cruise**) and a length, then **Build journey**. Loom walks a smooth BPM/energy curve,
choosing each next song to stay close to the last in genre and key (Camelot wheel), avoiding back-to-back
artists, and, if ticked, finishes with a full album from your library that sits where the curve ends. The graph
shows each song's BPM against the target. **Try another** gives a different take; **Save to Spotify** makes a
private `Loom · Journey …` playlist (re-saving the same name refills it).
