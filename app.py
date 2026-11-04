import asyncio
import re
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from urllib.parse import urlparse

import bot as discord_bot
import state
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

CATEGORY_NAME_RE = re.compile(r"^[a-z0-9_-]{1,50}$")
ALLOWED_TRACK_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
MAX_TITLE_LENGTH = 200
MAX_URL_LENGTH = 500
STATUS_QUEUE_PREVIEW = 20
STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
BOT_TIMEOUT = 15


def run_async(coro, timeout=BOT_TIMEOUT):
    loop = discord_bot.bot.loop
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def call_in_loop(func, *args):
    """Runs a synchronous bot function on the bot's event loop thread."""
    future = Future()

    def runner():
        try:
            future.set_result(func(*args))
        except Exception as e:
            future.set_exception(e)

    discord_bot.bot.loop.call_soon_threadsafe(runner)
    return future.result(timeout=BOT_TIMEOUT)


def json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def is_valid_snowflake(value):
    return isinstance(value, str) and value.isdigit() and len(value) <= 20


def is_valid_track_url(url):
    if len(url) > MAX_URL_LENGTH:
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and parsed.hostname in ALLOWED_TRACK_HOSTS


@app.before_request
def require_json_for_state_changes():
    # CSRF protection: HTML forms cannot send application/json, and a cross-site
    # fetch with this content type needs a CORS preflight, which this app never allows.
    if request.method in STATE_CHANGING_METHODS and not request.is_json:
        return jsonify({"ok": False, "message": "Content-Type must be application/json"}), 415
    return None


@app.errorhandler(FutureTimeoutError)
def handle_timeout(_error):
    return jsonify({"ok": False, "message": "Bot did not respond in time"}), 504


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/favicon.ico", methods=["GET"])
def favicon():
    # The page uses an inline icon; this only stops browsers from logging 404s.
    return "", 204


@app.route("/api/status", methods=["GET"])
def status():
    vc = state.bot_state["voice_client"]
    connected = vc is not None and vc.is_connected()
    queue = list(state.bot_state["queue"])
    return jsonify({
        "connected": connected,
        "is_playing": state.bot_state["is_playing"],
        "is_paused": vc.is_paused() if vc else False,
        "current_track": state.bot_state["current_track"],
        "current_category": state.bot_state["current_category"],
        "elapsed": discord_bot.elapsed_seconds(),
        "queue": [{"title": t.get("title") or t["url"], "url": t["url"]} for t in queue[:STATUS_QUEUE_PREVIEW]],
        "queue_length": len(queue),
        "loop": state.bot_state["loop"],
        "shuffle": state.bot_state["shuffle"],
        "guild_id": state.bot_state["guild_id"],
        "channel_id": state.bot_state["channel_id"],
        "channel_name": vc.channel.name if connected else None,
    })


@app.route("/api/guilds", methods=["GET"])
def guilds():
    return jsonify(discord_bot.get_guilds())


@app.route("/api/guilds/<guild_id>/channels", methods=["GET"])
def voice_channels(guild_id):
    if not is_valid_snowflake(guild_id):
        return jsonify([])
    return jsonify(discord_bot.get_voice_channels(guild_id))


@app.route("/api/join", methods=["POST"])
def join():
    data = json_body()
    guild_id = str(data.get("guild_id", ""))
    channel_id = str(data.get("channel_id", ""))
    if not is_valid_snowflake(guild_id) or not is_valid_snowflake(channel_id):
        return jsonify({"ok": False, "message": "guild_id and channel_id required"}), 400
    ok, msg = run_async(discord_bot.join_channel(guild_id, channel_id), timeout=discord_bot.JOIN_TIMEOUT)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/leave", methods=["POST"])
def leave():
    ok = run_async(discord_bot.leave_channel())
    return jsonify({"ok": ok})


@app.route("/api/play/category", methods=["POST"])
def play_category():
    category = json_body().get("category")
    if not isinstance(category, str) or not category:
        return jsonify({"ok": False, "message": "category required"}), 400
    ok, msg = run_async(discord_bot.play_category(category))
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/play/track", methods=["POST"])
def play_track():
    data = json_body()
    category = data.get("category")
    index = data.get("index")
    if not isinstance(category, str) or not isinstance(index, int) or isinstance(index, bool):
        return jsonify({"ok": False, "message": "category and index required"}), 400
    tracks = state.get_category_tracks(category)
    if not 0 <= index < len(tracks):
        return jsonify({"ok": False, "message": "Track not found"}), 404
    ok, msg = run_async(discord_bot.play_single_track(dict(tracks[index]), category))
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/stop", methods=["POST"])
def stop():
    ok = call_in_loop(discord_bot.stop_music)
    return jsonify({"ok": ok})


@app.route("/api/pause", methods=["POST"])
def pause():
    ok = call_in_loop(discord_bot.pause_music)
    return jsonify({"ok": ok})


@app.route("/api/resume", methods=["POST"])
def resume():
    ok = call_in_loop(discord_bot.resume_music)
    return jsonify({"ok": ok})


@app.route("/api/skip", methods=["POST"])
def skip():
    ok = call_in_loop(discord_bot.skip_track)
    return jsonify({"ok": ok})


@app.route("/api/loop", methods=["POST"])
def loop():
    state.bot_state["loop"] = bool(json_body().get("loop", False))
    return jsonify({"ok": True, "loop": state.bot_state["loop"]})


@app.route("/api/shuffle", methods=["POST"])
def shuffle():
    enabled = call_in_loop(discord_bot.set_shuffle, bool(json_body().get("shuffle", False)))
    return jsonify({"ok": True, "shuffle": enabled})


@app.route("/api/categories", methods=["GET"])
def categories():
    return jsonify(state.get_categories())


@app.route("/api/categories", methods=["POST"])
def add_category():
    name = json_body().get("name", "")
    if not isinstance(name, str):
        name = ""
    name = name.strip().replace(" ", "_").lower()
    if not name:
        return jsonify({"ok": False, "message": "name required"}), 400
    if not CATEGORY_NAME_RE.match(name):
        return jsonify({"ok": False, "message": "Use only letters, digits, spaces, - and _ (max 50)"}), 400
    ok = state.add_category(name)
    return jsonify({"ok": ok, "message": "Category added" if ok else "Already exists"})


@app.route("/api/categories/<name>", methods=["DELETE"])
def delete_category(name):
    ok = state.delete_category(name)
    return jsonify({"ok": ok})


@app.route("/api/categories/<category>/tracks", methods=["POST"])
def add_track(category):
    if not CATEGORY_NAME_RE.match(category):
        return jsonify({"ok": False, "message": "Invalid category"}), 400
    data = json_body()
    url = data.get("url", "")
    title = data.get("title", "")
    if not isinstance(url, str) or not isinstance(title, str):
        return jsonify({"ok": False, "message": "url and title must be strings"}), 400
    url = url.strip()
    title = title.strip()[:MAX_TITLE_LENGTH]
    if not url:
        return jsonify({"ok": False, "message": "url required"}), 400
    if not is_valid_track_url(url):
        return jsonify({"ok": False, "message": "Only YouTube links are allowed"}), 400

    playlist = discord_bot.is_playlist_url(url)
    if title and not playlist:
        tracks = [{"url": url, "title": title}]
    else:
        try:
            tracks = discord_bot.fetch_tracks(url)
        except Exception as e:
            print(f"Could not read {url} from YouTube: {e!r}")
            if playlist:
                return jsonify({"ok": False, "message": "Could not read this playlist from YouTube"}), 502
            tracks = [{"url": url, "title": url}]
        if not tracks:
            return jsonify({"ok": False, "message": "No playable videos found"}), 400
    for track in tracks:
        track["title"] = track["title"][:MAX_TITLE_LENGTH]
    state.add_tracks(category, tracks)
    return jsonify({"ok": True, "added": len(tracks)})


@app.route("/api/categories/<category>/tracks/<int:index>", methods=["DELETE"])
def remove_track(category, index):
    ok = state.remove_track(category, index)
    return jsonify({"ok": ok})
