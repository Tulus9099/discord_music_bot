import copy
import json
import os
import threading

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playlists.json")
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
        data = copy.deepcopy(DEFAULT_DATA)
        _save(data)
        return data
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _save(data):
    tmp_file = DATA_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_file, DATA_FILE)

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
