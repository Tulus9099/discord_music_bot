import asyncio
import shlex
import threading

import discord
import pytest

import app as web
import bot as discord_bot
import state


REAL_CALL_IN_LOOP = web.call_in_loop


class FakeVoiceClient:
    def __init__(self, playing=False, paused=False):
        self.playing = playing
        self.paused = paused
        self.connected = True
        self.source = None
        self.stopped = False
        self.channel = None

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing

    def is_paused(self):
        return self.paused

    def play(self, source, after=None):
        self.source = source
        self.playing = True

    def stop(self):
        self.stopped = True
        self.playing = False
        self.paused = False

    def pause(self):
        self.playing = False
        self.paused = True

    def resume(self):
        self.playing = True
        self.paused = False

    async def disconnect(self, force=False):
        self.connected = False


class FakePCMSource(discord.AudioSource):
    def __init__(self, *args, **kwargs):
        pass

    def read(self):
        return b""


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DATA_FILE", str(tmp_path / "playlists.json"))
    monkeypatch.setattr(web, "run_async", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(web, "call_in_loop", lambda func, *args: func(*args))
    original = dict(state.bot_state)
    yield
    state.bot_state.clear()
    state.bot_state.update(original)


@pytest.fixture
def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_index_renders(client):
    assert client.get("/").status_code == 200


def test_default_categories_created(client):
    r = client.get("/api/categories")
    assert set(r.get_json()) == {"combat_music", "ambient_music", "storm_music"}


def test_default_data_not_mutated(client):
    client.post("/api/categories", json={"name": "boss fight"})
    assert "boss_fight" not in state.DEFAULT_DATA["categories"]


def test_add_category_normalizes_name(client):
    r = client.post("/api/categories", json={"name": "Boss Fight"})
    assert r.get_json()["ok"] is True
    assert "boss_fight" in client.get("/api/categories").get_json()


@pytest.mark.parametrize("name", [
    "<img src=x onerror=alert(1)>",
    "a'); alert(1); //",
    "../etc",
    "x" * 51,
])
def test_add_category_rejects_unsafe_names(client, name):
    r = client.post("/api/categories", json={"name": name})
    assert r.status_code == 400


def test_duplicate_category(client):
    r = client.post("/api/categories", json={"name": "combat_music"})
    assert r.get_json()["ok"] is False


def test_delete_category(client):
    assert client.delete("/api/categories/storm_music", json={}).get_json()["ok"] is True
    assert client.delete("/api/categories/storm_music", json={}).get_json()["ok"] is False


@pytest.mark.parametrize("body", ["not json", [1, 2], {}])
def test_malformed_body_returns_400_not_500(client, body):
    for path in ["/api/categories", "/api/join", "/api/play/category", "/api/play/track",
                 "/api/categories/combat_music/tracks"]:
        if isinstance(body, str):
            r = client.post(path, data=body, content_type="application/json")
        else:
            r = client.post(path, json=body)
        assert r.status_code == 400, path


@pytest.mark.parametrize("method,path", [
    ("post", "/api/stop"),
    ("post", "/api/leave"),
    ("post", "/api/skip"),
    ("post", "/api/pause"),
    ("post", "/api/loop"),
    ("post", "/api/categories"),
    ("delete", "/api/categories/combat_music"),
])
@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"])
def test_state_changes_require_json_csrf(client, method, path, content_type):
    kwargs = {"data": "name=x"} if content_type else {}
    if content_type:
        kwargs["content_type"] = content_type
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 415
    assert "combat_music" in client.get("/api/categories").get_json()


@pytest.mark.parametrize("method", ["put", "patch"])
def test_get_only_routes_reject_other_methods(client, method):
    assert getattr(client, method)("/api/status", json={}).status_code == 405


def test_add_and_remove_track(client):
    r = client.post("/api/categories/combat_music/tracks", json={"url": YT_URL, "title": "Song"})
    assert r.get_json()["ok"] is True
    tracks = client.get("/api/categories").get_json()["combat_music"]
    assert tracks == [{"url": YT_URL, "title": "Song"}]
    assert client.delete("/api/categories/combat_music/tracks/0", json={}).get_json()["ok"] is True
    assert client.delete("/api/categories/combat_music/tracks/0", json={}).get_json()["ok"] is False


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "http://127.0.0.1:8080/admin",
    "http://169.254.169.254/latest/meta-data/",
    "https://youtube.com.evil.example/watch?v=x",
    "javascript:alert(1)",
    "https://www.youtube.com/watch?v=" + "a" * 600,
])
def test_add_track_rejects_non_youtube_urls(client, url):
    r = client.post("/api/categories/combat_music/tracks", json={"url": url})
    assert r.status_code == 400


def test_add_track_accepts_youtu_be(client):
    r = client.post("/api/categories/combat_music/tracks", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.get_json()["ok"] is True


def test_play_track_requires_connection(client):
    client.post("/api/categories/combat_music/tracks", json={"url": YT_URL})
    r = client.post("/api/play/track", json={"category": "combat_music", "index": 0})
    assert r.get_json() == {"ok": False, "message": "Bot is not in a voice channel"}


def test_play_track_out_of_range(client):
    r = client.post("/api/play/track", json={"category": "combat_music", "index": 5})
    assert r.status_code == 404


def test_play_track_rejects_arbitrary_track_payload(client):
    r = client.post("/api/play/track", json={"track": {"url": "file:///etc/passwd"}})
    assert r.status_code == 400


def test_play_track_uses_volume_transformer(client, monkeypatch):
    monkeypatch.setattr(discord_bot, "get_audio_url", lambda url: ("https://audio", "Title", {}))
    monkeypatch.setattr(discord, "FFmpegPCMAudio", FakePCMSource)
    vc = FakeVoiceClient()
    state.bot_state["voice_client"] = vc
    state.bot_state["volume"] = 0.3
    client.post("/api/categories/combat_music/tracks", json={"url": YT_URL})

    r = client.post("/api/play/track", json={"category": "combat_music", "index": 0})

    assert r.get_json()["ok"] is True
    assert isinstance(vc.source, discord.PCMVolumeTransformer)
    assert vc.source.volume == pytest.approx(0.3)
    assert state.bot_state["current_track"]["title"] == "Title"

    client.post("/api/volume", json={"volume": 0.8})
    assert vc.source.volume == pytest.approx(0.8)


@pytest.mark.parametrize("value,expected", [(2, 1.0), (-1, 0.0), (0.25, 0.25)])
def test_volume_is_clamped(client, value, expected):
    assert client.post("/api/volume", json={"volume": value}).get_json()["volume"] == expected


@pytest.mark.parametrize("value", ["abc", None, "nan"])
def test_volume_rejects_invalid(client, value):
    assert client.post("/api/volume", json={"volume": value}).status_code == 400


def test_stop_works_while_paused(client):
    vc = FakeVoiceClient(paused=True)
    state.bot_state["voice_client"] = vc
    state.bot_state["queue"] = [{"url": YT_URL}]
    assert client.post("/api/stop", json={}).get_json()["ok"] is True
    assert vc.stopped
    assert state.bot_state["queue"] == []


def test_pause_resume_skip(client):
    vc = FakeVoiceClient(playing=True)
    state.bot_state["voice_client"] = vc
    assert client.post("/api/pause", json={}).get_json()["ok"] is True
    assert client.get("/api/status").get_json()["is_paused"] is True
    assert client.post("/api/resume", json={}).get_json()["ok"] is True
    assert client.post("/api/skip", json={}).get_json()["ok"] is True
    assert vc.stopped


def test_leave_resets_state(client):
    state.bot_state["voice_client"] = FakeVoiceClient(playing=True)
    state.bot_state["queue"] = [{"url": YT_URL}]
    assert client.post("/api/leave", json={}).get_json()["ok"] is True
    assert state.bot_state["voice_client"] is None
    assert state.bot_state["queue"] == []
    assert client.post("/api/leave", json={}).get_json()["ok"] is False


def test_status_when_disconnected(client):
    s = client.get("/api/status").get_json()
    assert s["connected"] is False
    assert s["is_paused"] is False


def test_loop_toggle(client):
    assert client.post("/api/loop", json={"loop": True}).get_json()["loop"] is True


@pytest.mark.parametrize("payload", [
    {"guild_id": "abc", "channel_id": "1"},
    {"guild_id": "1"},
    {"guild_id": "1; drop", "channel_id": "2"},
])
def test_join_validates_ids(client, payload):
    assert client.post("/api/join", json=payload).status_code == 400


def test_voice_channels_invalid_guild_id(client):
    r = client.get("/api/guilds/not-a-number/channels")
    assert r.status_code == 200
    assert r.get_json() == []


def test_no_cors_headers(client):
    r = client.get("/api/status", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_trusted_hosts_blocks_foreign_host(client):
    web.app.config["TRUSTED_HOSTS"] = ["localhost", "127.0.0.1"]
    try:
        assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
        assert client.get("/api/status", headers={"Host": "localhost:5000"}).status_code == 200
    finally:
        web.app.config["TRUSTED_HOSTS"] = None


def test_message_content_intent_disabled():
    assert discord_bot.bot.intents.message_content is False
    assert discord_bot.bot.intents.voice_states is True


def test_ffmpeg_headers_are_quoted():
    opts = discord_bot.build_ffmpeg_before_opts({"User-Agent": "x'; rm -rf / #"})
    parts = shlex.split(opts)
    assert parts[-2] == "-headers"
    assert parts[-1] == "User-Agent: x'; rm -rf / #\r\n"


def test_join_already_in_channel(client, monkeypatch):
    channel = discord.VoiceChannel.__new__(discord.VoiceChannel)
    channel.id = 2
    guild = type("Guild", (), {"get_channel": lambda self, cid: channel})()
    monkeypatch.setattr(discord_bot.bot, "get_guild", lambda gid: guild)
    vc = FakeVoiceClient()
    vc.channel = channel
    state.bot_state["voice_client"] = vc
    r = client.post("/api/join", json={"guild_id": "1", "channel_id": "2"})
    assert r.get_json() == {"ok": True, "message": "Already in this channel"}


def test_connect_with_retry_retries_then_raises(monkeypatch):
    calls = []

    class FailingChannel:
        async def connect(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("boom")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(discord_bot.asyncio, "sleep", no_sleep)
    coro = discord_bot.connect_with_retry(FailingChannel())
    with pytest.raises(RuntimeError):
        asyncio.run(coro)
    assert len(calls) == 3


def test_call_in_loop_runs_on_bot_loop(monkeypatch):
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(discord_bot.bot, "loop", loop)
    try:
        assert REAL_CALL_IN_LOOP(lambda x: (x, threading.current_thread() is thread), 5) == (5, True)
        with pytest.raises(ValueError):
            REAL_CALL_IN_LOOP(int, "abc")
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()
