import json
import os
import threading

DATA_FILE = "playlists.json"
_lock = threading.Lock()

DEFAULT_DATA = {
    "categories": {
        "combat_music": [],
        "ambient_music": [],
        "storm_music": []
    }
}

def _load():
    if not os.path.exists(DATA_FILE):
        _save(DEFAULT_DATA)
        return DEFAULT_DATA
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def _save(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_categories():
    with _lock:
        return _load()["categories"]

def get_category_tracks(category):
    with _lock:
        data = _load()
        return data["categories"].get(category, [])

def add_track(category, url, title=""):
    with _lock:
        data = _load()
        if category not in data["categories"]:
            data["categories"][category] = []
        track = {"url": url, "title": title or url}
        data["categories"][category].append(track)
        _save(data)

def remove_track(category, index):
    with _lock:
        data = _load()
        if category in data["categories"]:
            tracks = data["categories"][category]
            if 0 <= index < len(tracks):
                tracks.pop(index)
                _save(data)
                return True
    return False

def add_category(name):
    with _lock:
        data = _load()
        if name not in data["categories"]:
            data["categories"][name] = []
            _save(data)
            return True
    return False

def delete_category(name):
    with _lock:
        data = _load()
        if name in data["categories"]:
            del data["categories"][name]
            _save(data)
            return True
    return False


bot_state = {
    "voice_client": None,
    "current_track": None,
    "current_category": None,
    "queue": [],
    "is_playing": False,
    "loop": False,
    "volume": 0.5,
    "guild_id": None,
    "channel_id": None,
}
