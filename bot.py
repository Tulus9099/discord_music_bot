import discord
import asyncio
import shlex
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


def get_audio_url(youtube_url):
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
        if "entries" in info:
            info = info["entries"][0]
        headers = info.get("http_headers") or {}
        return info["url"], info.get("title", youtube_url), headers


def build_ffmpeg_before_opts(headers):
    if not headers:
        return FFMPEG_BEFORE_OPTS
    header_str = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    return f"{FFMPEG_BEFORE_OPTS} -headers {shlex.quote(header_str)}"


async def play_next(guild_id):
    vc = state.bot_state["voice_client"]
    if vc is None:
        return

    queue = state.bot_state["queue"]

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
        loop = asyncio.get_running_loop()
        audio_url, title, headers = await loop.run_in_executor(
            None, get_audio_url, track["url"]
        )
        track["title"] = title
        state.bot_state["current_track"] = track

        before_opts = build_ffmpeg_before_opts(headers)

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(
                audio_url,
                before_options=before_opts,
                options=FFMPEG_OPTS,
            ),
            volume=state.bot_state["volume"],
        )

        def after_play(error):
            if error:
                print(f"Player error: {error}")
            asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

        vc.play(source, after=after_play)
        state.bot_state["is_playing"] = True
    except Exception as e:
        print(f"Error playing track: {e}")
        state.bot_state["is_playing"] = False


async def connect_with_retry(channel, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            return await channel.connect(timeout=30.0, reconnect=True, self_deaf=True)
        except Exception:
            if attempt == attempts:
                raise
            await asyncio.sleep(2)


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
    except Exception as e:
        return False, f"Failed to connect: {e}"

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

    state.bot_state["current_category"] = category
    state.bot_state["queue"] = list(tracks)
    state.bot_state["current_track"] = None

    if vc.is_playing():
        vc.stop()
    else:
        await play_next(state.bot_state["guild_id"])
    return True, "Playing category"


async def play_single_track(track):
    vc = state.bot_state["voice_client"]
    if vc is None or not vc.is_connected():
        return False, "Bot is not in a voice channel"

    state.bot_state["queue"] = [track]
    state.bot_state["current_track"] = None

    if vc.is_playing():
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
        return True
    return False


def pause_music():
    vc = state.bot_state["voice_client"]
    if vc and vc.is_playing():
        vc.pause()
        state.bot_state["is_playing"] = False
        return True
    return False


def resume_music():
    vc = state.bot_state["voice_client"]
    if vc and vc.is_paused():
        vc.resume()
        state.bot_state["is_playing"] = True
        return True
    return False


def skip_track():
    vc = state.bot_state["voice_client"]
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        return True
    return False


def set_volume(volume):
    vc = state.bot_state["voice_client"]
    state.bot_state["volume"] = volume
    if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = volume
    return True


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


@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")


def run_bot(token):
    bot.run(token)
