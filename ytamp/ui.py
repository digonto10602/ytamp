"""The curses interface: transport, analyser, search results and queue."""
from __future__ import annotations

import curses
import queue
import time

from . import theme as th
from .audio import SpectrumTap
from .config import KNOWN_BROWSERS, detect_browsers
from .player import MpvPlayer
from .playlist import Playlist, Track
from .search import Searcher, looks_like_url
from .util import ellipsize, fmt_count, fmt_time, open_in_browser

BLOCKS = " ▁▂▃▄▅▆▇█"
VIS_MODES = ("spectrum", "scope", "vu", "off")
PANE_SEARCH, PANE_QUEUE = 0, 1

HELP_LINES = [
    ("SEARCH & NAVIGATION", ""),
    ("/", "search YouTube (or paste a video / playlist link)"),
    ("Tab", "switch between the Results and Queue panes"),
    ("j k  ↑ ↓", "move the cursor"),
    ("g G", "jump to the top / bottom"),
    ("PgUp PgDn", "page through the list"),
    ("", ""),
    ("PLAYBACK", ""),
    ("Enter", "play the selected track now"),
    ("a", "add the selected result to the queue"),
    ("A", "add every result to the queue"),
    ("Space", "play / pause"),
    ("n p", "next / previous track"),
    ("← →", "seek 5 seconds"),
    ("Shift ← →", "seek 30 seconds"),
    ("+ -", "volume up / down"),
    ("m", "mute"),
    ("s r", "shuffle / repeat (off → all → one)"),
    ("", ""),
    ("QUEUE", ""),
    ("d", "remove the selected track from the queue"),
    ("c", "clear the queue"),
    ("J K", "move the selected track down / up"),
    ("", ""),
    ("OTHER", ""),
    ("o", "watch the current track on youtube.com"),
    ("O", "watch the selected track on youtube.com"),
    ("v", "cycle the visualiser (spectrum / scope / VU / off)"),
    ("L", "sign in with browser cookies, or go anonymous"),
    ("?", "toggle this help"),
    ("q", "quit (the queue is saved automatically)"),
]


class App:
    def __init__(self, stdscr, config):
        self.stdscr = stdscr
        self.config = config
        self.theme = th.Theme(config.get("ui", "palette", "gradient"))
        self.playlist = Playlist()
        self.searcher = Searcher(config)
        self.player = MpvPlayer(config, on_end=self._on_track_end, on_log=self.notify)
        self.tap = SpectrumTap(config)

        self.results: list[Track] = []
        self.pane = PANE_SEARCH
        self.cursor = {PANE_SEARCH: 0, PANE_QUEUE: 0}
        self.scroll = {PANE_SEARCH: 0, PANE_QUEUE: 0}

        self.vis_mode = config.get("ui", "vis_mode", "spectrum")
        if self.vis_mode not in VIS_MODES:
            self.vis_mode = "spectrum"
        self.vis_height = int(config.get("ui", "vis_height", 9))

        self.searching = False
        self.last_query = ""
        self.prompt: dict | None = None
        self.menu: dict | None = None
        self.show_help = False
        self.status = ""
        self.status_until = 0.0
        self.running = True
        self.restore = True          # load the saved queue on start
        self.events: queue.Queue = queue.Queue()
        self.hits: dict = {}          # mouse hit regions -> action
        self._spin = 0

    # --- helpers --------------------------------------------------------
    def notify(self, message: str, seconds: float = 4.0) -> None:
        self.status = message
        self.status_until = time.time() + seconds

    def _on_track_end(self) -> None:
        # Called from the mpv reader thread; hand it to the main loop.
        self.events.put(("advance", None))

    def items(self, pane=None):
        pane = self.pane if pane is None else pane
        return self.results if pane == PANE_SEARCH else self.playlist.tracks

    def selected(self) -> Track | None:
        items = self.items()
        index = self.cursor[self.pane]
        return items[index] if 0 <= index < len(items) else None

    def safe_addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        """addstr that never raises at the screen edge."""
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        if x < 0:
            text, x = text[-x:], 0
        # Writing the very last cell of the last row makes curses throw and can
        # abandon the rest of the line, so keep one cell in reserve there.
        limit = width - x - (1 if y == height - 1 else 0)
        text = text[: max(0, limit)]
        if not text:
            return
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass   # bottom-right cell always errors; harmless

    # --- playback -------------------------------------------------------
    def play_index(self, index: int) -> None:
        if not (0 <= index < len(self.playlist)):
            return
        self.playlist.index = index
        track = self.playlist.tracks[index]
        self.player.load(track.url or track.watch_url)
        self.notify(f"Loading: {track.title}", 3)

    def play_track(self, track: Track) -> None:
        """Queue a track (if new) and start it."""
        for i, existing in enumerate(self.playlist.tracks):
            if existing.id and existing.id == track.id:
                self.play_index(i)
                return
        self.playlist.add(track)
        self.play_index(len(self.playlist) - 1)

    def advance(self, auto: bool = False) -> None:
        index = self.playlist.next_index(auto=auto)
        if index is None:
            self.player.stop_playback()
            self.notify("End of queue")
            return
        self.play_index(index)

    def previous(self) -> None:
        index = self.playlist.prev_index()
        if index is not None:
            self.play_index(index)

    # --- search ---------------------------------------------------------
    def start_search(self, query: str) -> None:
        query = query.strip()
        if not query:
            return
        self.last_query = query
        self.searching = True
        self.results = []
        self.cursor[PANE_SEARCH] = 0
        self.scroll[PANE_SEARCH] = 0
        self.pane = PANE_SEARCH
        label = "Opening link" if looks_like_url(query) else "Searching"
        self.notify(f"{label}: {query}", 30)
        self.searcher.search_async(query, lambda t, e: self.events.put(("results", (t, e))))

    def _on_results(self, payload) -> None:
        tracks, error = payload
        self.searching = False
        self.results = tracks
        self.cursor[PANE_SEARCH] = 0
        self.scroll[PANE_SEARCH] = 0
        if error:
            self.notify(error, 8)
        else:
            self.notify(f"{len(tracks)} result{'s' if len(tracks) != 1 else ''}"
                        f" for “{self.last_query}”", 4)

    # --- auth -----------------------------------------------------------
    def open_auth_menu(self) -> None:
        items = [("Anonymous (no login)", lambda: self._set_auth("none", ""))]
        detected = detect_browsers()
        for browser in detected:
            items.append((f"Use cookies from {browser}",
                          lambda b=browser: self._set_auth("browser", b)))
        for browser in KNOWN_BROWSERS:
            if browser not in detected:
                items.append((f"Use cookies from {browser} (not detected)",
                              lambda b=browser: self._set_auth("browser", b)))
        items.append(("Use a cookies.txt file…", self._prompt_cookie_file))
        self.menu = {
            "title": "YouTube sign-in",
            "note": "Cookies are read from your local browser profile and only "
                    "ever sent to YouTube. Passwords are not supported by yt-dlp.",
            "items": items,
            "index": 0,
        }

    def _set_auth(self, mode: str, browser: str) -> None:
        self.config.set("auth", "mode", mode)
        if browser:
            self.config.set("auth", "browser", browser)
        self.config.save()
        self.notify(f"Sign-in: {self.config.auth_label()} — restarting player", 5)
        self._restart_player()

    def _prompt_cookie_file(self) -> None:
        self.prompt = {
            "label": "cookies.txt path",
            "buffer": self.config.get("auth", "cookie_file", ""),
            "on_accept": self._accept_cookie_file,
        }

    def _accept_cookie_file(self, value: str) -> None:
        value = value.strip()
        if not value:
            return
        self.config.set("auth", "cookie_file", value)
        self.config.set("auth", "mode", "cookiefile")
        self.config.save()
        self.notify(f"Sign-in: cookie file — restarting player", 5)
        self._restart_player()

    def _restart_player(self) -> None:
        """mpv takes cookie options at launch, so auth changes need a restart."""
        position = self.player.get("time-pos", 0)
        current = self.playlist.current
        self.player.stop()
        self.player = MpvPlayer(self.config, on_end=self._on_track_end, on_log=self.notify)
        if self.player.start() and current:
            self.player.load(current.url or current.watch_url)
            if position > 2:
                self.events.put(("reseek", position))

    # --- main loop ------------------------------------------------------
    def run(self) -> None:
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        self.stdscr.timeout(33)               # ~30 fps for a smooth analyser
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass
        self.theme.setup()

        if not self.player.start():
            self.notify("mpv failed to start - playback is unavailable", 20)
        self.tap.start()
        if self.restore and self.playlist.load():
            self.notify(f"Restored {len(self.playlist)} queued track"
                        f"{'s' if len(self.playlist) != 1 else ''} — press / to search", 6)
        else:
            self.notify("Press / to search YouTube, ? for help", 8)

        while self.running:
            self._drain_events()
            self.draw()
            try:
                key = self.stdscr.getch()
            except curses.error:
                key = -1
            if key != -1:
                self.handle_key(key)

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "results":
                self._on_results(payload)
            elif kind == "advance":
                self.advance(auto=True)
            elif kind == "reseek":
                self.player.seek(payload)

    def shutdown(self) -> None:
        self.running = False
        self.playlist.save()
        self.config.set("ui", "vis_mode", self.vis_mode)
        self.config.set("player", "volume", int(self.player.get("volume", 70)))
        self.config.save()
        self.searcher.cancel()
        self.tap.stop()
        self.player.stop()

    # --- layout ---------------------------------------------------------
    def layout(self) -> dict:
        height, width = self.stdscr.getmaxyx()
        chrome = 7                       # 3 header + seek + volume + tabs + status
        available = max(0, height - chrome)
        vis = 0 if self.vis_mode == "off" else max(0, min(self.vis_height, available - 3))
        if self.vis_mode == "vu":
            vis = min(vis, 4)
        return {
            "h": height, "w": width,
            "vis_top": 3, "vis_h": vis,
            "seek": 3 + vis,
            "vol": 4 + vis,
            "tabs": 5 + vis,
            "list_top": 6 + vis,
            "list_h": max(0, height - (6 + vis) - 1),
            "status": height - 1,
        }

    # --- drawing --------------------------------------------------------
    def draw(self) -> None:
        self.tap.set_active(self.player.status() == "PLAYING")
        self.stdscr.erase()
        self.hits = {}
        box = self.layout()
        # Repaint every cell each frame. Relying on erase() alone leaves stale
        # glyphs behind when a shorter string overwrites a longer one.
        blank = " " * box["w"]
        for row in range(box["h"]):
            self.safe_addstr(row, 0, blank)
        self.draw_header(box)
        if box["vis_h"] > 0:
            self.draw_vis(box)
        self.draw_seek(box)
        self.draw_volume(box)
        self.draw_tabs(box)
        self.draw_list(box)
        self.draw_status(box)
        if self.menu:
            self.draw_menu(box)
        elif self.show_help:
            self.draw_help(box)
        self.stdscr.noutrefresh()
        curses.doupdate()

    def draw_header(self, box) -> None:
        width = box["w"]
        status = self.player.status()
        auth = self.config.auth_label()
        title = " ♫ YTAMP · youtube terminal amp "
        right = f" {auth} · {status} "
        pad = max(0, width - len(title) - len(right))
        self.safe_addstr(0, 0, (title + " " * pad + right)[:width], self.theme.attr(th.P_TITLE, bold=True))

        track = self.playlist.current
        media = self.player.get("media-title") or (track.title if track else "")
        if not media or "watch?v=" in str(media):
            media = track.title if track else "nothing playing"
        icon = {"PLAYING": "▶", "PAUSED": "‖", "BUFFER": "◌",
                "STOPPED": "■", "DEAD": "✗"}.get(status, "■")
        self.safe_addstr(1, 1, icon, self.theme.attr(th.P_PLAY if status == "PLAYING" else th.P_DIM, bold=True))
        self.safe_addstr(1, 3, ellipsize(str(media), max(0, width - 4)),
                         self.theme.attr(th.P_ACCENT, bold=True))

        # Transport detail line, ending in a clickable "watch on youtube" button.
        position = self.player.get("time-pos", 0)
        duration = self.player.get("duration", 0) or (track.duration if track else 0)
        bitrate = self.player.get("audio-bitrate", 0)
        parts = [f"{fmt_time(position)} / {fmt_time(duration)}"]
        if track and track.uploader:
            parts.append(track.uploader)
        if bitrate:
            parts.append(f"{int(bitrate / 1000)} kbps")
        if self.playlist.tracks:
            parts.append(f"track {self.playlist.index + 1}/{len(self.playlist)}")
        line = "  ·  ".join(parts)
        self.safe_addstr(2, 3, ellipsize(line, max(0, width - 26)), self.theme.attr(th.P_DIM, dim=True))

        button = " ▶ WATCH VIDEO (o) "
        bx = width - len(button) - 1
        if bx > 4:
            self.safe_addstr(2, bx, button, self.theme.attr(th.P_BTN, bold=True))
            self.hits["watch"] = (2, bx, bx + len(button))

    def draw_vis(self, box) -> None:
        top, height, width = box["vis_top"], box["vis_h"], box["w"]
        if self.vis_mode == "scope":
            self.draw_scope(top, height, width)
            return
        if self.vis_mode == "vu":
            self.draw_vu(top, height, width)
            return

        configured = int(self.config.get("ui", "bands", 0))
        bands = configured if configured > 0 else max(8, (width - 2) // 2)
        self.tap.set_bands(bands)
        levels, peaks, _scope, _vu = self.tap.snapshot()
        bands = min(bands, len(levels))

        for band in range(bands):
            x = 1 + band * 2
            if x >= width - 1:
                break
            level = max(0.0, min(1.0, levels[band]))
            peak = max(0.0, min(1.0, peaks[band]))
            filled = level * height
            peak_cell = int(peak * height)
            for row in range(height):
                from_bottom = height - 1 - row
                y = top + row
                remainder = filled - from_bottom
                fraction = (from_bottom + 0.5) / max(1, height)
                if remainder >= 1.0:
                    self.safe_addstr(y, x, BLOCKS[8], self.theme.spectrum_attr(fraction))
                elif remainder > 0.05:
                    self.safe_addstr(y, x, BLOCKS[max(1, int(remainder * 8))],
                                     self.theme.spectrum_attr(fraction))
                elif peak_cell == from_bottom and peak > 0.02:
                    self.safe_addstr(y, x, "▔", self.theme.attr(th.P_DIM, dim=True))

        if self.tap.error:
            self.safe_addstr(top + height // 2, 2, f" {self.tap.error} ",
                             self.theme.attr(th.P_WARN))

    def draw_scope(self, top, height, width) -> None:
        _levels, _peaks, scope, _vu = self.tap.snapshot()
        if not scope:
            return
        middle = top + height // 2
        previous = None
        for x in range(1, width - 1):
            index = int((x - 1) / max(1, width - 2) * (len(scope) - 1))
            value = max(-1.0, min(1.0, scope[index]))
            y = int(round(middle - value * (height - 1) / 2))
            y = max(top, min(top + height - 1, y))
            attr = self.theme.spectrum_attr(abs(value))
            if previous is not None and abs(previous - y) > 1:
                step = 1 if y > previous else -1
                for fill in range(previous + step, y, step):
                    self.safe_addstr(fill, x, "│", attr)
            self.safe_addstr(y, x, "─" if abs(value) < 0.02 else "█", attr)
            previous = y

    def draw_vu(self, top, height, width) -> None:
        _levels, _peaks, _scope, vu = self.tap.snapshot()
        span = max(4, width - 10)
        for offset, (label, value) in enumerate((("L", vu[0]), ("R", vu[1]))):
            y = top + offset
            if y >= top + height:
                break
            self.safe_addstr(y, 1, label, self.theme.attr(th.P_ACCENT, bold=True))
            filled = int(max(0.0, min(1.0, value)) * span)
            for i in range(span):
                char = "█" if i < filled else "┄"
                attr = self.theme.spectrum_attr(i / span) if i < filled else self.theme.attr(th.P_DIM, dim=True)
                self.safe_addstr(y, 3 + i, char, attr)

    def draw_seek(self, box) -> None:
        y, width = box["seek"], box["w"]
        position = self.player.get("time-pos", 0)
        duration = self.player.get("duration", 0)
        span = max(4, width - 4)
        fraction = (position / duration) if duration else 0.0
        fraction = max(0.0, min(1.0, fraction))
        head = int(fraction * (span - 1))
        bar = ["─"] * span
        for i in range(head):
            bar[i] = "━"
        bar[head] = "●"
        self.safe_addstr(y, 2, "".join(bar), self.theme.attr(th.P_BAR))
        self.hits["seek"] = (y, 2, 2 + span)

    def draw_volume(self, box) -> None:
        y, width = box["vol"], box["w"]
        volume = int(self.player.get("volume", 70))
        muted = bool(self.player.get("mute", False))
        span = min(24, max(6, width // 4))
        filled = int(volume / 130 * span)
        label = "MUTE" if muted else f"{volume:3d}%"
        self.safe_addstr(y, 2, "VOL ", self.theme.attr(th.P_DIM, dim=True))
        for i in range(span):
            char = "█" if i < filled else "░"
            attr = self.theme.attr(th.P_WARN if muted else th.P_BAR)
            self.safe_addstr(y, 6 + i, char, attr if i < filled else self.theme.attr(th.P_DIM, dim=True))
        self.safe_addstr(y, 7 + span, label, self.theme.attr(th.P_WARN if muted else th.P_ACCENT))
        self.hits["vol"] = (y, 6, 6 + span)

        flags = []
        flags.append(("SHUFFLE", self.playlist.shuffle))
        flags.append((f"REPEAT:{self.playlist.repeat.upper()}", self.playlist.repeat != "off"))
        flags.append((f"VIS:{self.vis_mode.upper()}", self.vis_mode != "off"))
        x = 13 + span
        for text, on in flags:
            if x + len(text) + 2 >= width:
                break
            self.safe_addstr(y, x, text, self.theme.attr(th.P_OK if on else th.P_DIM,
                                                         bold=on, dim=not on))
            x += len(text) + 2

    def draw_tabs(self, box) -> None:
        y, width = box["tabs"], box["w"]
        if self.prompt:
            label = f" {self.prompt['label']}: "
            self.safe_addstr(y, 0, label, self.theme.attr(th.P_TAB, bold=True))
            text = self.prompt["buffer"]
            room = max(0, width - len(label) - 2)
            shown = text[-room:] if len(text) > room else text
            self.safe_addstr(y, len(label), shown + "█", self.theme.attr(th.P_ACCENT, bold=True))
            return
        spinner = "◜◝◞◟"[(self._spin // 6) % 4] if self.searching else ""
        left = f" RESULTS ({len(self.results)}){spinner and ' ' + spinner} "
        right = f" QUEUE ({len(self.playlist)}) "
        self.safe_addstr(y, 0, left, self.theme.attr(th.P_TAB if self.pane == PANE_SEARCH else th.P_DIM,
                                                     bold=self.pane == PANE_SEARCH,
                                                     dim=self.pane != PANE_SEARCH))
        self.safe_addstr(y, len(left) + 1, right,
                         self.theme.attr(th.P_TAB if self.pane == PANE_QUEUE else th.P_DIM,
                                         bold=self.pane == PANE_QUEUE,
                                         dim=self.pane != PANE_QUEUE))
        self.hits["tab_search"] = (y, 0, len(left))
        self.hits["tab_queue"] = (y, len(left) + 1, len(left) + 1 + len(right))
        hint = "/ search   ↵ play   a queue   ? help"
        if width > len(left) + len(right) + len(hint) + 6:
            self.safe_addstr(y, width - len(hint) - 2, hint, self.theme.attr(th.P_DIM, dim=True))

    def draw_list(self, box) -> None:
        top, height, width = box["list_top"], box["list_h"], box["w"]
        if height <= 0:
            return
        items = self.items()
        cursor = self.cursor[self.pane]

        # Keep the cursor inside the viewport.
        scroll = self.scroll[self.pane]
        if cursor < scroll:
            scroll = cursor
        elif cursor >= scroll + height:
            scroll = cursor - height + 1
        scroll = max(0, min(scroll, max(0, len(items) - height)))
        self.scroll[self.pane] = scroll
        self.hits["list"] = (top, top + height, scroll)

        if not items:
            message = ("No results yet — press / to search YouTube"
                       if self.pane == PANE_SEARCH else
                       "Queue is empty — press a on a result to add it")
            self.safe_addstr(top + 1, 3, message, self.theme.attr(th.P_DIM, dim=True))
            return

        dur_w, views_w, num_w = 7, 8, 4
        uploader_w = 22 if width > 78 else (14 if width > 60 else 0)
        title_w = max(10, width - num_w - dur_w - views_w - uploader_w - 4)

        for row in range(height):
            index = scroll + row
            if index >= len(items):
                break
            track = items[index]
            y = top + row
            is_cursor = index == cursor
            is_playing = (self.pane == PANE_QUEUE and index == self.playlist.index) or \
                         (self.pane == PANE_SEARCH and self.playlist.current is not None
                          and track.id and track.id == self.playlist.current.id)

            if is_cursor:
                attr = self.theme.attr(th.P_SEL, bold=True)
                self.safe_addstr(y, 0, " " * width, attr)
            elif is_playing:
                attr = self.theme.attr(th.P_PLAY, bold=True)
            else:
                attr = self.theme.attr(th.P_DIM)

            marker = "▶" if is_playing else " "
            x = 0
            self.safe_addstr(y, x, f" {marker}{index + 1:>2} ", attr)
            x += num_w + 2
            self.safe_addstr(y, x, ellipsize(track.title, title_w), attr)
            x += title_w + 1
            if uploader_w:
                self.safe_addstr(y, x, ellipsize(track.uploader, uploader_w - 1),
                                 attr if is_cursor else self.theme.attr(th.P_DIM, dim=True))
                x += uploader_w
            self.safe_addstr(y, x, f"{fmt_count(track.views):>7}",
                             attr if is_cursor else self.theme.attr(th.P_DIM, dim=True))
            x += views_w
            label = "LIVE" if track.live else fmt_time(track.duration)
            self.safe_addstr(y, x, f"{label:>6}", attr)

        if len(items) > height:
            self.safe_addstr(top, width - 1, "↑" if scroll > 0 else " ",
                             self.theme.attr(th.P_DIM, dim=True))
            self.safe_addstr(top + height - 1, width - 1,
                             "↓" if scroll + height < len(items) else " ",
                             self.theme.attr(th.P_DIM, dim=True))

    def draw_status(self, box) -> None:
        y, width = box["status"], box["w"]
        if time.time() > self.status_until:
            self.status = ""
        text = self.status or "/ search   space play/pause   n p track   ← → seek   o watch   ? help   q quit"
        attr = self.theme.attr(th.P_WARN if self.status else th.P_DIM,
                               dim=not self.status)
        self.safe_addstr(y, 1, ellipsize(text, width - 2), attr)

    def draw_menu(self, box) -> None:
        items = self.menu["items"]
        note = self.menu.get("note", "")
        width = min(box["w"] - 4, 72)
        wrapped = self._wrap(note, width - 4) if note else []
        height = len(items) + len(wrapped) + 4
        top = max(0, (box["h"] - height) // 2)
        left = max(0, (box["w"] - width) // 2)
        self._box(top, left, height, width, self.menu["title"])
        y = top + 1
        for line in wrapped:
            self.safe_addstr(y, left + 2, line, self.theme.attr(th.P_DIM, dim=True))
            y += 1
        if wrapped:
            y += 1
        for i, (label, _action) in enumerate(items):
            selected = i == self.menu["index"]
            attr = self.theme.attr(th.P_SEL, bold=True) if selected else self.theme.attr(th.P_DIM)
            self.safe_addstr(y, left + 1, " " * (width - 2), attr if selected else 0)
            self.safe_addstr(y, left + 2, ("› " if selected else "  ") + ellipsize(label, width - 6), attr)
            y += 1
        self.safe_addstr(top + height - 1, left + 2, " ↑↓ choose  ↵ select  esc cancel ",
                         self.theme.attr(th.P_DIM, dim=True))

    def draw_help(self, box) -> None:
        width = min(box["w"] - 4, 64)
        height = min(box["h"] - 2, len(HELP_LINES) + 3)
        top = max(0, (box["h"] - height) // 2)
        left = max(0, (box["w"] - width) // 2)
        self._box(top, left, height, width, "Keys")
        for i, (key, description) in enumerate(HELP_LINES[: height - 3]):
            y = top + 1 + i
            if not key and not description:
                continue
            if not description:
                self.safe_addstr(y, left + 2, key, self.theme.attr(th.P_ACCENT, bold=True))
            else:
                self.safe_addstr(y, left + 2, f"{key:>11}", self.theme.attr(th.P_OK, bold=True))
                self.safe_addstr(y, left + 15, ellipsize(description, width - 17),
                                 self.theme.attr(th.P_DIM))
        self.safe_addstr(top + height - 1, left + 2, " press ? or esc to close ",
                         self.theme.attr(th.P_DIM, dim=True))

    def _box(self, top, left, height, width, title) -> None:
        for row in range(height):
            self.safe_addstr(top + row, left, " " * width, self.theme.attr(th.P_DIM))
        border = self.theme.attr(th.P_ACCENT)
        self.safe_addstr(top, left, "┌" + "─" * (width - 2) + "┐", border)
        self.safe_addstr(top + height - 1, left, "└" + "─" * (width - 2) + "┘", border)
        for row in range(1, height - 1):
            self.safe_addstr(top + row, left, "│", border)
            self.safe_addstr(top + row, left + width - 1, "│", border)
        self.safe_addstr(top, left + 2, f" {title} ", self.theme.attr(th.P_ACCENT, bold=True))

    @staticmethod
    def _wrap(text, width):
        words, lines, current = text.split(), [], ""
        for word in words:
            if len(current) + len(word) + 1 > width:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            lines.append(current)
        return lines

    # --- input ----------------------------------------------------------
    def move_cursor(self, delta: int, absolute: int | None = None) -> None:
        items = self.items()
        if not items:
            self.cursor[self.pane] = 0
            return
        target = absolute if absolute is not None else self.cursor[self.pane] + delta
        self.cursor[self.pane] = max(0, min(len(items) - 1, target))

    def handle_key(self, key: int) -> None:
        self._spin += 1
        if key == curses.KEY_RESIZE:
            self.stdscr.erase()
            return
        if self.prompt is not None:
            self.handle_prompt_key(key)
            return
        if self.menu is not None:
            self.handle_menu_key(key)
            return
        if key == curses.KEY_MOUSE:
            self.handle_mouse()
            return
        if self.show_help:
            if key in (27, ord("?"), ord("q"), 10, 13):
                self.show_help = False
            return

        box = self.layout()
        page = max(1, box["list_h"] - 1)
        seek_step = float(self.config.get("player", "seek_step", 5))
        seek_big = float(self.config.get("player", "seek_step_big", 30))

        if key in (ord("q"),):
            self.running = False
        elif key == ord("?"):
            self.show_help = True
        elif key == ord("/"):
            self.prompt = {"label": "Search YouTube", "buffer": "",
                           "on_accept": self.start_search}
        elif key == ord("\t"):
            self.pane = PANE_QUEUE if self.pane == PANE_SEARCH else PANE_SEARCH
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move_cursor(1)
        elif key in (curses.KEY_UP, ord("k")):
            self.move_cursor(-1)
        elif key == curses.KEY_NPAGE:
            self.move_cursor(page)
        elif key == curses.KEY_PPAGE:
            self.move_cursor(-page)
        elif key == ord("g") or key == curses.KEY_HOME:
            self.move_cursor(0, absolute=0)
        elif key == ord("G") or key == curses.KEY_END:
            self.move_cursor(0, absolute=len(self.items()))
        elif key in (10, 13, curses.KEY_ENTER):
            self._activate()
        elif key == ord("a"):
            self._add_selected()
        elif key == ord("A"):
            self._add_all_results()
        elif key == ord(" "):
            self.player.toggle_pause()
        elif key == ord("n"):
            self.advance(auto=False)
        elif key == ord("p"):
            self.previous()
        elif key == curses.KEY_RIGHT:
            self.player.seek(seek_step)
        elif key == curses.KEY_LEFT:
            self.player.seek(-seek_step)
        elif key == curses.KEY_SRIGHT:
            self.player.seek(seek_big)
        elif key == curses.KEY_SLEFT:
            self.player.seek(-seek_big)
        elif key in (ord("+"), ord("=")):
            volume = self.player.set_volume(int(self.player.get("volume", 70)) + 5)
            self.notify(f"Volume {volume}%", 1.5)
        elif key in (ord("-"), ord("_")):
            volume = self.player.set_volume(int(self.player.get("volume", 70)) - 5)
            self.notify(f"Volume {volume}%", 1.5)
        elif key == ord("m"):
            self.player.toggle_mute()
        elif key == ord("s"):
            self.playlist.set_shuffle(not self.playlist.shuffle)
            self.notify(f"Shuffle {'on' if self.playlist.shuffle else 'off'}", 2)
        elif key == ord("r"):
            self.notify(f"Repeat: {self.playlist.cycle_repeat()}", 2)
        elif key == ord("v"):
            self.vis_mode = VIS_MODES[(VIS_MODES.index(self.vis_mode) + 1) % len(VIS_MODES)]
            self.notify(f"Visualiser: {self.vis_mode}", 2)
        elif key == ord("o"):
            self._watch(self.playlist.current)
        elif key == ord("O"):
            self._watch(self.selected())
        elif key == ord("d"):
            self._remove_selected()
        elif key == ord("c"):
            self.playlist.clear()
            self.player.stop_playback()
            self.notify("Queue cleared", 2)
        elif key == ord("J"):
            if self.pane == PANE_QUEUE:
                self.cursor[PANE_QUEUE] = self.playlist.move(self.cursor[PANE_QUEUE], 1)
        elif key == ord("K"):
            if self.pane == PANE_QUEUE:
                self.cursor[PANE_QUEUE] = self.playlist.move(self.cursor[PANE_QUEUE], -1)
        elif key == ord("L"):
            self.open_auth_menu()

    def _activate(self) -> None:
        if self.pane == PANE_QUEUE:
            if self.playlist.tracks:
                self.play_index(self.cursor[PANE_QUEUE])
            return
        track = self.selected()
        if track:
            self.play_track(track)

    def _add_selected(self) -> None:
        track = self.selected()
        if not track or self.pane != PANE_SEARCH:
            return
        self.playlist.add(track)
        self.notify(f"Queued: {track.title}", 3)
        self.move_cursor(1)

    def _add_all_results(self) -> None:
        if not self.results:
            self.notify("No results to queue", 2)
            return
        added = self.playlist.extend(self.results)
        self.notify(f"Queued {added} track{'s' if added != 1 else ''}", 3)

    def _remove_selected(self) -> None:
        if self.pane != PANE_QUEUE or not self.playlist.tracks:
            return
        index = self.cursor[PANE_QUEUE]
        playing = index == self.playlist.index
        self.playlist.remove(index)
        self.cursor[PANE_QUEUE] = min(index, max(0, len(self.playlist) - 1))
        if playing:
            if self.playlist.tracks:
                self.play_index(min(index, len(self.playlist) - 1))
            else:
                self.player.stop_playback()

    def _watch(self, track: Track | None) -> None:
        if not track:
            self.notify("Nothing to watch yet", 2)
            return
        if open_in_browser(track.watch_url):
            self.notify(f"Opened in browser: {track.watch_url}", 4)
        else:
            self.notify(f"No browser found. URL: {track.watch_url}", 10)

    def handle_prompt_key(self, key: int) -> None:
        prompt = self.prompt
        if key in (27,):                                   # esc
            self.prompt = None
        elif key in (10, 13, curses.KEY_ENTER):
            value = prompt["buffer"]
            self.prompt = None
            prompt["on_accept"](value)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            prompt["buffer"] = prompt["buffer"][:-1]
        elif key == 21:                                    # ctrl-u
            prompt["buffer"] = ""
        elif key == 23:                                    # ctrl-w
            prompt["buffer"] = prompt["buffer"].rstrip().rsplit(" ", 1)[0] if " " in prompt["buffer"].rstrip() else ""
        elif 32 <= key < 0x110000:
            try:
                prompt["buffer"] += chr(key)
            except ValueError:
                pass

    def handle_menu_key(self, key: int) -> None:
        menu = self.menu
        count = len(menu["items"])
        if key in (27, ord("q")):
            self.menu = None
        elif key in (curses.KEY_DOWN, ord("j")):
            menu["index"] = (menu["index"] + 1) % count
        elif key in (curses.KEY_UP, ord("k")):
            menu["index"] = (menu["index"] - 1) % count
        elif key in (10, 13, curses.KEY_ENTER):
            action = menu["items"][menu["index"]][1]
            self.menu = None
            action()

    def handle_mouse(self) -> None:
        try:
            _id, x, y, _z, state = curses.getmouse()
        except curses.error:
            return

        if state & curses.BUTTON4_PRESSED:
            self.move_cursor(-3)
            return
        if hasattr(curses, "BUTTON5_PRESSED") and state & curses.BUTTON5_PRESSED:
            self.move_cursor(3)
            return
        if not state & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
            return

        for name in ("watch", "tab_search", "tab_queue"):
            region = self.hits.get(name)
            if region and y == region[0] and region[1] <= x < region[2]:
                if name == "watch":
                    self._watch(self.playlist.current)
                elif name == "tab_search":
                    self.pane = PANE_SEARCH
                else:
                    self.pane = PANE_QUEUE
                return

        seek = self.hits.get("seek")
        if seek and y == seek[0] and seek[1] <= x < seek[2]:
            self.player.seek_percent((x - seek[1]) / max(1, seek[2] - seek[1] - 1) * 100)
            return

        volume = self.hits.get("vol")
        if volume and y == volume[0] and volume[1] <= x < volume[2]:
            fraction = (x - volume[1] + 1) / max(1, volume[2] - volume[1])
            self.player.set_volume(int(fraction * 130))
            return

        lst = self.hits.get("list")
        if lst and lst[0] <= y < lst[1]:
            index = lst[2] + (y - lst[0])
            if index < len(self.items()):
                # Click to select, click again on the same row to play it.
                if self.cursor[self.pane] == index:
                    self._activate()
                else:
                    self.cursor[self.pane] = index
