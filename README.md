# Discord Play Music

A Discord bot that plays music from YouTube in a voice channel. You control it from a simple web panel that runs locally, so all players in the channel hear the same music, for example during an RPG session.

## Features

- Join and leave a chosen voice channel from the panel.
- Music categories (`combat_music`, `ambient_music` and `storm_music` by default), plus your own categories that you can add and delete.
- Add YouTube links to categories.
- Play a whole category or a single track.
- Pause, resume, skip and stop tracks, loop playback and adjust the volume.
- See the track that is playing now and the queue length.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended) or `pip`
- A Discord bot account with a token

You don't need to install FFmpeg manually. The `static-ffmpeg` package downloads it automatically on first run.

## Discord bot setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2. In the **Bot** tab, generate a token (**Reset Token**) and copy it.
3. The bot doesn't need any *Privileged Gateway Intents*, so you can leave them disabled.
4. In the **OAuth2 → URL Generator** tab:
   - under *Scopes*, select `bot`,
   - under *Bot Permissions*, select `View Channels`, `Connect` and `Speak`.
5. Open the generated link and invite the bot to your server.

## Installation

```bash
git clone https://github.com/keeeleee/discord_music_bot.git
cd discord_music_bot
uv sync
```

If you use `pip` instead of `uv`:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Copy the example file and add your token:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `DISCORD_TOKEN` | none (required) | Bot token from the Developer Portal |
| `FLASK_HOST` | `127.0.0.1` | Address the web panel listens on |
| `FLASK_PORT` | `5000` | Web panel port |

> ⚠️ Never commit the `.env` file, because it contains your bot token. The file is already listed in `.gitignore`. If your token leaks, reset it right away in the Developer Portal.

## Running

```bash
uv run main.py
```

Or, if you use `pip` with the virtual environment activated:

```bash
python main.py
```

Then open `http://localhost:5000` (or the port you set in `FLASK_PORT`).

## Usage

1. Select a server and a voice channel, then click **Join**.
2. Select a category or add a new one.
3. Add YouTube links to the category. The title is optional.
4. Click **▶ Play Category** to play the whole category, or **▶** next to a single track.
5. Control playback with the Pause, Skip, Stop and Loop buttons and the volume slider.

Playlists are saved to `playlists.json` in the project directory. The file is created automatically on first run and is not committed to the repository.

## Security

- The web panel **has no login**. By default it listens only on `127.0.0.1`, so you can open it only from your own computer.
- Set `FLASK_HOST=0.0.0.0` only if you trust everyone on your network, because then anyone who knows the address can control the bot. Don't expose the panel to the internet.
- Only YouTube links (`youtube.com`, `youtu.be`, `music.youtube.com`) can be added to playlists.

## Tests

```bash
uv run pytest
```

## Project structure

```
main.py               # entry point: loads .env, starts the Flask panel and the bot
bot.py                # Discord bot logic: voice channels, playback (yt-dlp + FFmpeg)
app.py                # REST API for the web panel
state.py              # player state and saving playlists to playlists.json
templates/index.html  # web panel UI
tests/                # tests (pytest)
```

## Troubleshooting

- **`DISCORD_TOKEN is not set`**: the `.env` file is missing or has no token in it.
- **The bot's server isn't in the list**: make sure the bot is invited to the server and is online (the console should show `Bot logged in as ...`).
- **A track doesn't play**: YouTube changes often, so update yt-dlp with `uv lock --upgrade-package yt-dlp && uv sync`.
