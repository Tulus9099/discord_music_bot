import discord
import asyncio
import itertools
import logging
import random
import shlex
import time
from urllib.parse import urlparse
import yt_dlp
import static_ffmpeg
import state

static_ffmpeg.add_paths()

intents = discord.Intents.default()
intents.voice_states = True

bot = discord.Client(intents=intents)

YTDL_OPTIONS = {
    "format": "bestaudio[acodec=opus]/bestaudio[ext=webm]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "extract_flat": False,
    "source_address": "0.0.0.0",
}

# Used when adding tracks: reads titles (and playlist entries) without resolving audio streams.
METADATA_OPTIONS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "extract_flat": "in_playlist",
}
MAX_PLAYLIST_TRACKS = 200
UNAVAILABLE_TITLES = {"[Private video]", "[Deleted video]"}

FFMPEG_BEFORE_OPTS = (
    "-nostdin "
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_on_network_error 1 "
    "-reconnect_on_http_error 4xx,5xx "
    "-reconnect_delay_max 5 "
    "-analyzeduration 0 "
    "-probesize 32 "
    "-rw_timeout 15000000"
)
FFMPEG_OPTS = "-vn -bufsize 512k"

VOICE_CONNECT_TIMEOUT = 20.0
VOICE_CONNECT_ATTEMPTS = 2
VOICE_RETRY_DELAY = 2
# Upper bound for join_channel, so the web panel waits long enough for all attempts.
JOIN_TIMEOUT = VOICE_CONNECT_ATTEMPTS * (VOICE_CONNECT_TIMEOUT + VOICE_RETRY_DELAY) + 5

# Stream URLs from YouTube stay valid for hours; refresh prefetched ones after 30 minutes anyway.
PREFETCH_MAX_AGE = 30 * 60
AUTO_LEAVE_DELAY = 60

# YouTube URL -> (resolved_at, audio info). Only touched from the bot's event loop thread.
_prefetched = {}
# Keeps references to fire-and-forget tasks so they are not garbage collected mid-run.
_background_tasks = set()


def run_in_background(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def get_audio_url(youtube_url):
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return {
            "url": info["url"],
            "title": info.get("title", youtube_url),
            "acodec": info.get("acodec", ""),
            "headers": info.get("http_headers") or {},
            "duration": info.get("duration"),
        }


def is_playlist_url(url):
    return urlparse(url).path == "/playlist"


def fetch_tracks(url):
    """Returns [{url, title}] for a single video, or for every video of a playlist link."""
    with yt_dlp.YoutubeDL({**METADATA_OPTIONS, "noplaylist": not is_playlist_url(url)}) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries")
    if entries is None:
        return [{"url": url, "title": info.get("title") or url}]
    tracks = []
    for entry in itertools.islice(entries, MAX_PLAYLIST_TRACKS):
        if not entry or not entry.get("id") or entry.get("title") in UNAVAILABLE_TITLES:
            continue
        tracks.append({
            "url": f"https://www.youtube.com/watch?v={entry['id']}",
            "title": entry.get("title") or entry["id"],
        })
    return tracks


def build_ffmpeg_before_opts(headers):
    if not headers:
        return FFMPEG_BEFORE_OPTS
    header_str = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    return f"{FFMPEG_BEFORE_OPTS} -headers {shlex.quote(header_str)}"


def create_audio_source(audio_url, before_opts, acodec):
    # ffmpeg produces Opus itself, so no system libopus is needed.
    # YouTube usually serves Opus already, which is passed through without re-encoding.
    if acodec == "opus":
        return discord.FFmpegOpusAudio(audio_url, before_options=before_opts, options=FFMPEG_OPTS, codec="copy")
    return discord.FFmpegOpusAudio(audio_url, before_options=before_opts, options=FFMPEG_OPTS, bitrate=128)


def upcoming_track():
    if state.bot_state["loop"] and state.bot_state["current_track"]:
        return state.bot_state["current_track"]
    queue = state.bot_state["queue"]
    return queue[0] if queue else None


def take_prefetched(url):
    cached = _prefetched.pop(url, None)
    if cached and time.monotonic() - cached[0] < PREFETCH_MAX_AGE:
        return cached[1]
    return None


async def prefetch_next():
    """Resolves the next track's stream while the current one plays, to shorten the gap."""
    track = upcoming_track()
    if track is None or track["url"] in _prefetched:
        return
    try:
        info = await asyncio.get_running_loop().run_in_executor(None, get_audio_url, track["url"])
    except Exception as e:
        print(f"Prefetch failed for {track['url']}: {e!r}")
        return
    _prefetched.clear()
    _prefetched[track["url"]] = (time.monotonic(), info)


def reset_track_clock():
    state.bot_state["track_started_at"] = None
    state.bot_state["paused_at"] = None
    state.bot_state["paused_total"] = 0.0


def elapsed_seconds():
    started = state.bot_state["track_started_at"]
    if started is None:
        return None
    end = state.bot_state["paused_at"] or time.monotonic()
    return max(0.0, end - started - state.bot_state["paused_total"])


async def play_next(guild_id):
    vc = state.bot_state["voice_client"]
    if vc is None:
        return

    queue = state.bot_state["queue"]
    reset_track_clock()

    if state.bot_state["loop"] and state.bot_state["current_track"]:
        track = dict(state.bot_state["current_track"])
    elif queue:
        track = queue.pop(0)
        state.bot_state["current_track"] = track
    else:
        state.bot_state["is_playing"] = False
        state.bot_state["current_track"] = None
        return

    try:
        info = take_prefetched(track["url"])
        if info is None:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, get_audio_url, track["url"])
        track["title"] = info["title"]
        track["duration"] = info["duration"]
        state.bot_state["current_track"] = track

        before_opts = build_ffmpeg_before_opts(info["headers"])

        source = create_audio_source(info["url"], before_opts, info["acodec"])

        def after_play(error):
            if error:
                print(f"Player error: {error}")
            asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

        vc.play(source, after=after_play)
        state.bot_state["is_playing"] = True
        state.bot_state["track_started_at"] = time.monotonic()
        run_in_background(prefetch_next())
    except Exception as e:
        print(f"Error playing track: {e!r}")
        state.bot_state["is_playing"] = False


async def connect_with_retry(channel, attempts=VOICE_CONNECT_ATTEMPTS):
    for attempt in range(1, attempts + 1):
        try:
            return await channel.connect(timeout=VOICE_CONNECT_TIMEOUT, reconnect=True, self_deaf=True)
        except Exception as e:
            print(f"Voice connection attempt {attempt}/{attempts} failed: {e!r}")
            if attempt == attempts:
                raise
            await asyncio.sleep(VOICE_RETRY_DELAY)


async def join_channel(guild_id, channel_id):
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return False, "Guild not found"

    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return False, "Channel not found"

    if not isinstance(channel, discord.VoiceChannel):
        return False, "Not a voice channel"

    vc = state.bot_state["voice_client"]
    if vc and vc.is_connected() and vc.channel.id == channel.id:
        return True, "Already in this channel"

    try:
        if vc and vc.is_connected():
            await vc.move_to(channel)
        else:
            state.bot_state["voice_client"] = await connect_with_retry(channel)
    except TimeoutError:
        return False, "Timed out connecting to Discord voice server (check firewall/VPN)"
    except Exception as e:
        return False, f"Failed to connect: {e!r}"

    state.bot_state["guild_id"] = guild_id
    state.bot_state["channel_id"] = channel_id
    return True, "Joined channel"


async def leave_channel():
    vc = state.bot_state["voice_client"]
    if vc is None:
        return False
    state.bot_state["queue"] = []
    state.bot_state["current_track"] = None
    state.bot_state["is_playing"] = False
    state.bot_state["voice_client"] = None
    reset_track_clock()
    was_connected = vc.is_connected()
    await vc.disconnect(force=True)
    return was_connected


async def play_category(category):
    tracks = state.get_category_tracks(category)
    if not tracks:
        return False, "No tracks in category"

    vc = state.bot_state["voice_client"]
    if vc is None or not vc.is_connected():
        return False, "Bot is not in a voice channel"

    queue = list(tracks)
    if state.bot_state["shuffle"]:
        random.shuffle(queue)
    state.bot_state["current_category"] = category
    state.bot_state["queue"] = queue
    state.bot_state["current_track"] = None

    if vc.is_playing() or vc.is_paused():
        vc.stop()
    else:
        await play_next(state.bot_state["guild_id"])
    return True, "Playing category"


async def play_single_track(track, category=None):
    vc = state.bot_state["voice_client"]
    if vc is None or not vc.is_connected():
        return False, "Bot is not in a voice channel"

    state.bot_state["current_category"] = category
    state.bot_state["queue"] = [track]
    state.bot_state["current_track"] = None

    if vc.is_playing() or vc.is_paused():
        vc.stop()
    else:
        await play_next(state.bot_state["guild_id"])

    return True, "Playing track"


def stop_music():
    vc = state.bot_state["voice_client"]
    if vc and (vc.is_playing() or vc.is_paused()):
        state.bot_state["queue"] = []
        state.bot_state["current_track"] = None
        vc.stop()
        state.bot_state["is_playing"] = False
        reset_track_clock()
        return True
    return False


def pause_music():
    vc = state.bot_state["voice_client"]
    if vc and vc.is_playing():
        vc.pause()
        state.bot_state["is_playing"] = False
        state.bot_state["paused_at"] = time.monotonic()
        return True
    return False


def resume_music():
    vc = state.bot_state["voice_client"]
    if vc and vc.is_paused():
        vc.resume()
        state.bot_state["is_playing"] = True
        if state.bot_state["paused_at"] is not None:
            state.bot_state["paused_total"] += time.monotonic() - state.bot_state["paused_at"]
            state.bot_state["paused_at"] = None
        return True
    return False


def skip_track():
    vc = state.bot_state["voice_client"]
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        return True
    return False


def set_shuffle(enabled):
    state.bot_state["shuffle"] = enabled
    if enabled:
        random.shuffle(state.bot_state["queue"])
    return enabled


def get_guilds():
    return [{"id": str(g.id), "name": g.name} for g in bot.guilds]


def get_voice_channels(guild_id):
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return []
    return [
        {"id": str(c.id), "name": c.name}
        for c in guild.channels
        if isinstance(c, discord.VoiceChannel)
    ]


def has_listeners(channel):
    return any(not member.bot for member in channel.members)


@bot.event
async def on_voice_state_update(member, before, after):
    vc = state.bot_state["voice_client"]
    if member.bot or vc is None or not vc.is_connected():
        return
    if before.channel != vc.channel or has_listeners(vc.channel):
        return
    await asyncio.sleep(AUTO_LEAVE_DELAY)
    if state.bot_state["voice_client"] is vc and vc.is_connected() and not has_listeners(vc.channel):
        print("Everyone left the voice channel - leaving.")
        await leave_channel()


@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")


def run_bot(token, log_level=logging.INFO):
    bot.run(token, log_level=log_level)
