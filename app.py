import asyncio
import bot as discord_bot
import state
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


def run_async(coro):
    loop = discord_bot.bot.loop
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=15)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    vc = state.bot_state["voice_client"]
    current = state.bot_state["current_track"]
    return jsonify({
        "connected": vc is not None and vc.is_connected() if vc else False,
        "is_playing": state.bot_state["is_playing"],
        "is_paused": vc.is_paused() if vc else False,
        "current_track": current,
        "current_category": state.bot_state["current_category"],
        "queue_length": len(state.bot_state["queue"]),
        "loop": state.bot_state["loop"],
        "volume": state.bot_state["volume"],
        "guild_id": state.bot_state["guild_id"],
        "channel_id": state.bot_state["channel_id"],
    })


@app.route("/api/guilds")
def guilds():
    return jsonify(discord_bot.get_guilds())


@app.route("/api/guilds/<guild_id>/channels")
def voice_channels(guild_id):
    return jsonify(discord_bot.get_voice_channels(guild_id))


@app.route("/api/join", methods=["POST"])
def join():
    data = request.json
    guild_id = data.get("guild_id")
    channel_id = data.get("channel_id")
    if not guild_id or not channel_id:
        return jsonify({"ok": False, "message": "guild_id and channel_id required"}), 400
    ok, msg = run_async(discord_bot.join_channel(guild_id, channel_id))
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/leave", methods=["POST"])
def leave():
    ok = run_async(discord_bot.leave_channel())
    return jsonify({"ok": ok})


@app.route("/api/play/category", methods=["POST"])
def play_category():
    data = request.json
    category = data.get("category")
    if not category:
        return jsonify({"ok": False, "message": "category required"}), 400
    ok, msg = run_async(discord_bot.play_category(category))
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/play/track", methods=["POST"])
def play_track():
    data = request.json
    track = data.get("track")
    if not track:
        return jsonify({"ok": False, "message": "track required"}), 400
    ok, msg = run_async(discord_bot.play_single_track(track))
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/stop", methods=["POST"])
def stop():
    ok = run_async(discord_bot.stop_music())
    return jsonify({"ok": ok})


@app.route("/api/pause", methods=["POST"])
def pause():
    ok = run_async(discord_bot.pause_music())
    return jsonify({"ok": ok})


@app.route("/api/resume", methods=["POST"])
def resume():
    ok = run_async(discord_bot.resume_music())
    return jsonify({"ok": ok})


@app.route("/api/skip", methods=["POST"])
def skip():
    ok = run_async(discord_bot.skip_track())
    return jsonify({"ok": ok})


@app.route("/api/volume", methods=["POST"])
def volume():
    data = request.json
    vol = float(data.get("volume", 0.5))
    vol = max(0.0, min(1.0, vol))
    run_async(discord_bot.set_volume(vol))
    return jsonify({"ok": True, "volume": vol})


@app.route("/api/loop", methods=["POST"])
def loop():
    data = request.json
    state.bot_state["loop"] = bool(data.get("loop", False))
    return jsonify({"ok": True, "loop": state.bot_state["loop"]})


@app.route("/api/categories")
def categories():
    return jsonify(state.get_categories())


@app.route("/api/categories", methods=["POST"])
def add_category():
    data = request.json
    name = data.get("name", "").strip().replace(" ", "_").lower()
    if not name:
        return jsonify({"ok": False, "message": "name required"}), 400
    ok = state.add_category(name)
    return jsonify({"ok": ok, "message": "Category added" if ok else "Already exists"})


@app.route("/api/categories/<name>", methods=["DELETE"])
def delete_category(name):
    ok = state.delete_category(name)
    return jsonify({"ok": ok})


@app.route("/api/categories/<category>/tracks", methods=["POST"])
def add_track(category):
    data = request.json
    url = data.get("url", "").strip()
    title = data.get("title", "").strip()
    if not url:
        return jsonify({"ok": False, "message": "url required"}), 400
    state.add_track(category, url, title)
    return jsonify({"ok": True})


@app.route("/api/categories/<category>/tracks/<int:index>", methods=["DELETE"])
def remove_track(category, index):
    ok = state.remove_track(category, index)
    return jsonify({"ok": ok})
