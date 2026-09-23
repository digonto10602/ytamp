"""The Track model and the play queue (order, shuffle, repeat, persistence)."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .util import data_dir

REPEAT_MODES = ("off", "all", "one")


@dataclass
class Track:
    id: str
    title: str
    duration: float | None = None
    uploader: str = ""
    views: int | None = None
    live: bool = False
    url: str = ""

    def __post_init__(self):
        if not self.url:
            self.url = f"https://www.youtube.com/watch?v={self.id}"

    @property
    def watch_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}" if self.id else self.url

    @classmethod
    def from_entry(cls, entry: dict) -> "Track | None":
        """Build a Track from a yt-dlp flat-playlist entry, or None if unusable."""
        if not entry:
            return None
        vid = entry.get("id") or ""
        url = entry.get("url") or entry.get("webpage_url") or ""
        if not vid and "watch?v=" in url:
            vid = url.split("watch?v=")[-1].split("&")[0]
        if not vid and not url:
            return None
        duration = entry.get("duration")
        try:
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        return cls(
            id=vid,
            title=entry.get("title") or entry.get("fulltitle") or "(untitled)",
            duration=duration,
            uploader=entry.get("channel") or entry.get("uploader") or "",
            views=entry.get("view_count"),
            live=bool(entry.get("is_live")),
            url=url or f"https://www.youtube.com/watch?v={vid}",
        )


class Playlist:
    """An ordered queue with an independent shuffle order.

    Shuffle changes the order tracks are *visited*, not the order they are
    displayed, so the list on screen stays stable while shuffling.
    """

    def __init__(self):
        self.tracks: list[Track] = []
        self.index: int = -1
        self.shuffle: bool = False
        self.repeat: str = "off"
        self._order: list[int] = []

    def __len__(self) -> int:
        return len(self.tracks)

    @property
    def current(self) -> Track | None:
        if 0 <= self.index < len(self.tracks):
            return self.tracks[self.index]
        return None

    def add(self, track: Track, at_end: bool = True) -> int:
        if at_end:
            self.tracks.append(track)
            pos = len(self.tracks) - 1
        else:
            pos = max(0, self.index + 1)
            self.tracks.insert(pos, track)
            if self.index >= pos:
                self.index += 1
        self._reorder()
        return pos

    def extend(self, tracks) -> int:
        added = 0
        for track in tracks:
            if track:
                self.tracks.append(track)
                added += 1
        self._reorder()
        return added

    def remove(self, pos: int) -> None:
        if not (0 <= pos < len(self.tracks)):
            return
        self.tracks.pop(pos)
        if pos < self.index:
            self.index -= 1
        elif pos == self.index:
            self.index = min(self.index, len(self.tracks) - 1)
        self._reorder()

    def move(self, pos: int, delta: int) -> int:
        target = pos + delta
        if not (0 <= pos < len(self.tracks)) or not (0 <= target < len(self.tracks)):
            return pos
        self.tracks[pos], self.tracks[target] = self.tracks[target], self.tracks[pos]
        if self.index == pos:
            self.index = target
        elif self.index == target:
            self.index = pos
        self._reorder()
        return target

    def clear(self) -> None:
        self.tracks.clear()
        self.index = -1
        self._order.clear()

    def _reorder(self) -> None:
        self._order = list(range(len(self.tracks)))
        if self.shuffle:
            random.shuffle(self._order)
            # Keep the currently playing track at the head so "next" advances
            # into fresh material rather than possibly repeating it.
            if 0 <= self.index < len(self.tracks) and self.index in self._order:
                self._order.remove(self.index)
                self._order.insert(0, self.index)

    def set_shuffle(self, on: bool) -> None:
        self.shuffle = on
        self._reorder()

    def cycle_repeat(self) -> str:
        self.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % len(REPEAT_MODES)]
        return self.repeat

    def _step(self, delta: int, auto: bool) -> int | None:
        """Index of the next/previous track, or None when the queue ends."""
        if not self.tracks:
            return None
        if auto and self.repeat == "one":
            return self.index if self.index >= 0 else 0
        if not self._order:
            self._reorder()
        try:
            pos = self._order.index(self.index)
        except ValueError:
            return self._order[0] if self._order else None
        pos += delta
        if pos >= len(self._order):
            if self.repeat == "all" or not auto:
                # Reshuffle on wrap so a repeated shuffle isn't the same loop.
                if self.shuffle and self.repeat == "all":
                    self._reorder()
                return self._order[0]
            return None
        if pos < 0:
            return self._order[-1]
        return self._order[pos]

    def next_index(self, auto: bool = False) -> int | None:
        return self._step(1, auto)

    def prev_index(self) -> int | None:
        return self._step(-1, False)

    # --- persistence ----------------------------------------------------
    def save(self, name: str = "queue") -> Path | None:
        path = data_dir() / f"{name}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "index": self.index,
                "shuffle": self.shuffle,
                "repeat": self.repeat,
                "tracks": [asdict(t) for t in self.tracks],
            }
            path.write_text(json.dumps(payload, indent=1))
            return path
        except OSError:
            return None

    def load(self, name: str = "queue") -> bool:
        path = data_dir() / f"{name}.json"
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            return False
        fields = set(Track.__dataclass_fields__)
        self.tracks = [
            Track(**{k: v for k, v in t.items() if k in fields})
            for t in payload.get("tracks", [])
            if isinstance(t, dict) and (t.get("id") or t.get("url"))
        ]
        self.index = payload.get("index", -1)
        self.shuffle = bool(payload.get("shuffle", False))
        self.repeat = payload.get("repeat", "off")
        if self.repeat not in REPEAT_MODES:
            self.repeat = "off"
        self.index = min(self.index, len(self.tracks) - 1)
        self._reorder()
        return True
