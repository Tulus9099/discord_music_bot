class PlaylistRepository:
    """Categories (playlists) and their tracks."""

    def __init__(self, store):
        self._store = store

    def categories(self):
        return self._store.read()["categories"]

    def tracks(self, category):
        return self._store.read()["categories"].get(category, [])

    def add_tracks(self, category, tracks):
        entries = [_entry(track) for track in tracks]

        def change(data):
            data["categories"].setdefault(category, []).extend(entries)
            return None, True

        self._store.edit(change)

    def add_category(self, name):
        def change(data):
            if name in data["categories"]:
                return False, False
            data["categories"][name] = []
            return True, True

        return self._store.edit(change)

    def delete_category(self, name):
        def change(data):
            if name not in data["categories"]:
                return False, False
            del data["categories"][name]
            return True, True

        return self._store.edit(change)

    def remove_track(self, category, index):
        def change(data):
            tracks = data["categories"].get(category)
            if tracks is None or not 0 <= index < len(tracks):
                return False, False
            tracks.pop(index)
            return True, True

        return self._store.edit(change)

    def rename_track(self, category, index, title):
        def change(data):
            tracks = data["categories"].get(category)
            if tracks is None or not 0 <= index < len(tracks):
                return False, False
            tracks[index]["title"] = title
            return True, True

        return self._store.edit(change)

    def update_track(self, category, index, url, title):
        def change(data):
            tracks = data["categories"].get(category)
            if tracks is None or not 0 <= index < len(tracks):
                return False, False
            tracks[index] = {"url": url, "title": title}
            return True, True

        return self._store.edit(change)


def _entry(track):
    if track.get("file"):
        return {"file": track["file"], "title": track.get("title") or track["file"]}
    return {"url": track["url"], "title": track.get("title") or track["url"]}
