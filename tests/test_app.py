import asyncio
import shlex
import threading
import types

import discord
import pytest

import app as web
import bot as discord_bot
import state


REAL_CALL_IN_LOOP = web.call_in_loop
REAL_FETCH_TRACKS = discord_bot.fetch_tracks
# Deterministic stand-in for the random shuffler: reverses the order instead.
REVERSING_SHUFFLER = types.SimpleNamespace(shuffle=lambda items: items.reverse())


def audio_info(title="Title", duration=120):
    return {"url": "https://audio", "title": title, "acodec": "opus", "headers": {}, "duration": duration}


class FakeVoiceClient:
    def __init__(self, playing=False, paused=False):
        self.playing = playing
        self.paused = paused
        self.connected = True
        self.source = None
        self.stopped = False
        self.channel = types.SimpleNamespace(id=2, name="sesja", members=[])

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


class FakeOpusSource(discord.AudioSource):
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def read(self):
        return b""

    def is_opus(self):
        return True


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DATA_FILE", str(tmp_path / "playlists.json"))
    monkeypatch.setattr(web, "run_async", lambda coro, timeout=None: asyncio.run(coro))
    monkeypatch.setattr(web, "call_in_loop", lambda func, *args: func(*args))
    # Never reach YouTube from tests; the title is derived from the URL instead.
    monkeypatch.setattr(discord_bot, "fetch_tracks", lambda url: [{"url": url, "title": "YT " + url[-4:]}])
    # Fresh copies per test; monkeypatch restores the originals afterwards.
    monkeypatch.setattr(state, "bot_state", dict(state.bot_state))
    monkeypatch.setattr(discord_bot, "_prefetched", {})
    monkeypatch.setattr(discord_bot, "_background_tasks", set())


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(web.app.config, "TESTING", True)
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


def test_play_track_starts_playback(client, monkeypatch):
    monkeypatch.setattr(discord_bot, "get_audio_url", lambda url: audio_info())
    monkeypatch.setattr(discord, "FFmpegOpusAudio", FakeOpusSource)
    vc = FakeVoiceClient()
    state.bot_state["voice_client"] = vc
    client.post("/api/categories/combat_music/tracks", json={"url": YT_URL})

    r = client.post("/api/play/track", json={"category": "combat_music", "index": 0})

    assert r.get_json()["ok"] is True
    assert isinstance(vc.source, FakeOpusSource)
    assert state.bot_state["current_track"]["title"] == "Title"
    assert state.bot_state["is_playing"] is True


@pytest.mark.parametrize("acodec,expected", [("opus", {"codec": "copy"}), ("mp4a.40.2", {"bitrate": 128})])
def test_audio_source_copies_opus_and_encodes_other_codecs(monkeypatch, acodec, expected):
    monkeypatch.setattr(discord, "FFmpegOpusAudio", FakeOpusSource)
    source = discord_bot.create_audio_source("https://audio", "", acodec)
    assert expected.items() <= source.kwargs.items()


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


def test_trusted_hosts_blocks_foreign_host(client, monkeypatch):
    monkeypatch.setitem(web.app.config, "TRUSTED_HOSTS", ["localhost", "127.0.0.1"])
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/status", headers={"Host": "localhost:5000"}).status_code == 200


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
    assert len(calls) == discord_bot.VOICE_CONNECT_ATTEMPTS


def test_join_timeout_covers_all_connection_attempts(client, monkeypatch):
    seen = {}

    def fake_run_async(coro, timeout=None):
        coro.close()
        seen["timeout"] = timeout
        return True, "Joined channel"

    monkeypatch.setattr(web, "run_async", fake_run_async)
    client.post("/api/join", json={"guild_id": "1", "channel_id": "2"})
    worst_case = discord_bot.VOICE_CONNECT_ATTEMPTS * (
        discord_bot.VOICE_CONNECT_TIMEOUT + discord_bot.VOICE_RETRY_DELAY
    )
    assert seen["timeout"] > worst_case


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


def test_join_timeout_returns_readable_message(client, monkeypatch):
    channel = discord.VoiceChannel.__new__(discord.VoiceChannel)
    channel.id = 2
    guild = type("Guild", (), {"get_channel": lambda self, cid: channel})()
    monkeypatch.setattr(discord_bot.bot, "get_guild", lambda gid: guild)

    async def timeout(_channel):
        raise TimeoutError

    monkeypatch.setattr(discord_bot, "connect_with_retry", timeout)
    r = client.post("/api/join", json={"guild_id": "1", "channel_id": "2"})
    assert r.get_json()["ok"] is False
    assert "Timed out" in r.get_json()["message"]


@pytest.fixture
def sample_audio(tmp_path):
    import shutil
    import subprocess
    import static_ffmpeg
    static_ffmpeg.add_paths()
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    path = tmp_path / "tone.webm"
    subprocess.run(
        [ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:a", "libopus", str(path)],
        check=True,
    )
    return str(path)


@pytest.mark.parametrize("acodec", ["opus", "mp4a.40.2"])
def test_audio_source_produces_frames_with_real_ffmpeg(sample_audio, acodec):
    source = discord_bot.create_audio_source(sample_audio, "-nostdin", acodec)
    try:
        frames = [source.read() for _ in range(5)]
        assert source.is_opus()
        assert all(frames)
    finally:
        source.cleanup()


# ---------- Adding tracks: titles and playlists ----------

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL123"


def test_add_track_without_title_uses_youtube_title(client):
    client.post("/api/categories/combat_music/tracks", json={"url": YT_URL})
    tracks = client.get("/api/categories").get_json()["combat_music"]
    assert tracks == [{"url": YT_URL, "title": "YT " + YT_URL[-4:]}]


def test_add_track_with_title_skips_youtube_lookup(client, monkeypatch):
    def fail(url):
        raise AssertionError("should not query YouTube")

    monkeypatch.setattr(discord_bot, "fetch_tracks", fail)
    r = client.post("/api/categories/combat_music/tracks", json={"url": YT_URL, "title": "Mine"})
    assert r.get_json() == {"ok": True, "added": 1}


def test_add_playlist_adds_all_videos(client, monkeypatch):
    videos = [{"url": f"https://www.youtube.com/watch?v=v{i}", "title": f"Song {i}"} for i in range(3)]
    monkeypatch.setattr(discord_bot, "fetch_tracks", lambda url: [dict(v) for v in videos])
    r = client.post("/api/categories/ambient_music/tracks", json={"url": PLAYLIST_URL, "title": "ignored"})
    assert r.get_json() == {"ok": True, "added": 3}
    assert client.get("/api/categories").get_json()["ambient_music"] == videos


def test_youtube_failure_falls_back_to_url_for_single_video(client, monkeypatch):
    def offline(url):
        raise RuntimeError("offline")

    monkeypatch.setattr(discord_bot, "fetch_tracks", offline)
    assert client.post("/api/categories/combat_music/tracks", json={"url": YT_URL}).get_json()["ok"] is True
    assert client.get("/api/categories").get_json()["combat_music"][0]["title"] == YT_URL


def test_youtube_failure_for_playlist_is_reported(client, monkeypatch):
    def offline(url):
        raise RuntimeError("offline")

    monkeypatch.setattr(discord_bot, "fetch_tracks", offline)
    r = client.post("/api/categories/combat_music/tracks", json={"url": PLAYLIST_URL})
    assert r.status_code == 502
    assert client.get("/api/categories").get_json()["combat_music"] == []


def test_empty_playlist_is_rejected(client, monkeypatch):
    monkeypatch.setattr(discord_bot, "fetch_tracks", lambda url: [])
    assert client.post("/api/categories/combat_music/tracks", json={"url": PLAYLIST_URL}).status_code == 400


def test_fetched_titles_are_truncated(client, monkeypatch):
    monkeypatch.setattr(discord_bot, "fetch_tracks", lambda url: [{"url": url, "title": "x" * 500}])
    client.post("/api/categories/combat_music/tracks", json={"url": YT_URL})
    title = client.get("/api/categories").get_json()["combat_music"][0]["title"]
    assert len(title) == web.MAX_TITLE_LENGTH


def install_fake_youtube_dl(monkeypatch, info):
    """Replaces yt_dlp.YoutubeDL for one test; returns the options it was created with."""
    used_options = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            used_options.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            return info

    monkeypatch.setattr(discord_bot.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    return used_options


def test_fetch_tracks_reads_playlist_entries(monkeypatch):
    options = install_fake_youtube_dl(monkeypatch, {"entries": iter([
        {"id": "a1", "title": "First"},
        {"id": "b2", "title": "[Private video]"},
        None,
        {"id": "c3", "title": None},
    ])})
    tracks = REAL_FETCH_TRACKS(PLAYLIST_URL)
    assert tracks == [
        {"url": "https://www.youtube.com/watch?v=a1", "title": "First"},
        {"url": "https://www.youtube.com/watch?v=c3", "title": "c3"},
    ]
    assert options["noplaylist"] is False


def test_fetch_tracks_limits_playlist_size(monkeypatch):
    install_fake_youtube_dl(monkeypatch, {"entries": ({"id": f"v{i}", "title": str(i)} for i in range(1000))})
    assert len(REAL_FETCH_TRACKS(PLAYLIST_URL)) == discord_bot.MAX_PLAYLIST_TRACKS


def test_fetch_tracks_single_video_ignores_playlist_param(monkeypatch):
    options = install_fake_youtube_dl(monkeypatch, {"title": "One video"})
    url = YT_URL + "&list=PL123"
    assert REAL_FETCH_TRACKS(url) == [{"url": url, "title": "One video"}]
    assert options["noplaylist"] is True


# ---------- Shuffle, queue and status ----------

def connected_client_with_tracks(client, monkeypatch, count=5):
    monkeypatch.setattr(discord_bot, "get_audio_url", lambda url: audio_info(title=url[-2:]))
    monkeypatch.setattr(discord, "FFmpegOpusAudio", FakeOpusSource)
    for i in range(count):
        client.post("/api/categories/combat_music/tracks", json={"url": f"{YT_URL[:-2]}{i:02d}", "title": f"T{i}"})
    vc = FakeVoiceClient()
    state.bot_state["voice_client"] = vc
    return vc


def test_status_shows_queue_and_channel(client, monkeypatch):
    connected_client_with_tracks(client, monkeypatch, count=3)
    client.post("/api/play/category", json={"category": "combat_music"})
    s = client.get("/api/status").get_json()
    assert s["channel_name"] == "sesja"
    assert s["current_track"]["duration"] == 120
    assert s["current_category"] == "combat_music"
    assert [t["title"] for t in s["queue"]] == ["T1", "T2"]
    assert s["queue_length"] == 2
    assert s["shuffle"] is False


def test_status_queue_preview_is_limited(client):
    state.bot_state["queue"] = [{"url": f"{YT_URL}{i}"} for i in range(50)]
    s = client.get("/api/status").get_json()
    assert len(s["queue"]) == web.STATUS_QUEUE_PREVIEW
    assert s["queue_length"] == 50


def test_play_category_with_shuffle_randomizes_order(client, monkeypatch):
    connected_client_with_tracks(client, monkeypatch, count=8)
    monkeypatch.setattr(discord_bot, "_shuffler", REVERSING_SHUFFLER)
    client.post("/api/shuffle", json={"shuffle": True})
    client.post("/api/play/category", json={"category": "combat_music"})
    played = [state.bot_state["current_track"]["url"]] + [t["url"] for t in state.bot_state["queue"]]
    original = [t["url"] for t in client.get("/api/categories").get_json()["combat_music"]]
    assert sorted(played) == sorted(original)
    assert played != original


def test_enabling_shuffle_reshuffles_remaining_queue(client, monkeypatch):
    queue = [{"url": f"{YT_URL}{i}"} for i in range(10)]
    state.bot_state["queue"] = list(queue)
    monkeypatch.setattr(discord_bot, "_shuffler", REVERSING_SHUFFLER)
    r = client.post("/api/shuffle", json={"shuffle": True})
    assert r.get_json() == {"ok": True, "shuffle": True}
    assert state.bot_state["queue"] != queue
    assert sorted(t["url"] for t in state.bot_state["queue"]) == sorted(t["url"] for t in queue)


def test_play_single_track_marks_category(client, monkeypatch):
    connected_client_with_tracks(client, monkeypatch, count=2)
    client.post("/api/play/track", json={"category": "combat_music", "index": 1})
    assert state.bot_state["current_category"] == "combat_music"
    assert state.bot_state["current_track"]["url"].endswith("01")


def test_elapsed_time_excludes_pauses(client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(discord_bot.time, "monotonic", lambda: now[0])
    vc = connected_client_with_tracks(client, monkeypatch, count=1)
    client.post("/api/play/category", json={"category": "combat_music"})
    now[0] += 30
    client.post("/api/pause", json={})
    now[0] += 100
    assert client.get("/api/status").get_json()["elapsed"] == 30
    client.post("/api/resume", json={})
    now[0] += 5
    assert client.get("/api/status").get_json()["elapsed"] == 35
    client.post("/api/stop", json={})
    assert vc.stopped
    assert client.get("/api/status").get_json()["elapsed"] is None


# ---------- Prefetching the next track ----------

def test_next_track_is_prefetched_and_reused(client, monkeypatch):
    calls = []

    def resolve(url):
        calls.append(url)
        return audio_info(title="Resolved " + url[-2:])

    connected_client_with_tracks(client, monkeypatch, count=2)
    monkeypatch.setattr(discord_bot, "get_audio_url", resolve)

    async def scenario():
        await discord_bot.play_category("combat_music")
        await asyncio.gather(*discord_bot._background_tasks)
        assert calls == [f"{YT_URL[:-2]}00", f"{YT_URL[:-2]}01"]
        await discord_bot.play_next(None)
        assert len(calls) == 2, "the prefetched result should be used instead of resolving again"
        assert state.bot_state["current_track"]["title"] == "Resolved 01"

    asyncio.run(scenario())


def test_stale_prefetch_is_ignored(monkeypatch):
    now = [5000.0]
    monkeypatch.setattr(discord_bot.time, "monotonic", lambda: now[0])
    monkeypatch.setitem(discord_bot._prefetched, "u", (now[0], audio_info()))
    now[0] += discord_bot.PREFETCH_MAX_AGE + 1
    assert discord_bot.take_prefetched("u") is None


def test_failed_prefetch_does_not_break_playback(monkeypatch):
    def broken(url):
        raise RuntimeError("yt error")

    state.bot_state["queue"] = [{"url": YT_URL}]
    monkeypatch.setattr(discord_bot, "get_audio_url", broken)
    asyncio.run(discord_bot.prefetch_next())
    assert discord_bot._prefetched == {}


# ---------- Auto-leave ----------

def member(is_bot=False):
    return types.SimpleNamespace(bot=is_bot)


@pytest.mark.parametrize("remaining,should_leave", [([], True), ([member(is_bot=True)], True), ([member()], False)])
def test_auto_leave_when_channel_empties(monkeypatch, remaining, should_leave):
    monkeypatch.setattr(discord_bot, "AUTO_LEAVE_DELAY", 0)
    vc = FakeVoiceClient(playing=True)
    vc.channel.members = remaining
    state.bot_state["voice_client"] = vc
    before = types.SimpleNamespace(channel=vc.channel)
    after = types.SimpleNamespace(channel=None)
    asyncio.run(discord_bot.on_voice_state_update(member(), before, after))
    assert (state.bot_state["voice_client"] is None) is should_leave


def test_auto_leave_ignores_other_channels(monkeypatch):
    monkeypatch.setattr(discord_bot, "AUTO_LEAVE_DELAY", 0)
    vc = FakeVoiceClient()
    state.bot_state["voice_client"] = vc
    other = types.SimpleNamespace(channel=types.SimpleNamespace(id=99, members=[]))
    asyncio.run(discord_bot.on_voice_state_update(member(), other, types.SimpleNamespace(channel=None)))
    assert state.bot_state["voice_client"] is vc


def test_auto_leave_cancelled_when_someone_returns(monkeypatch):
    vc = FakeVoiceClient()
    state.bot_state["voice_client"] = vc
    before = types.SimpleNamespace(channel=vc.channel)

    async def someone_returns(_delay):
        vc.channel.members = [member()]

    monkeypatch.setattr(discord_bot.asyncio, "sleep", someone_returns)
    asyncio.run(discord_bot.on_voice_state_update(member(), before, types.SimpleNamespace(channel=None)))
    assert state.bot_state["voice_client"] is vc
