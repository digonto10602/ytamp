"""YouTube search and URL resolution, driven by the yt-dlp CLI.

Everything here runs off the UI thread. Callers hand in a callback that
receives (tracks, error) when the work finishes.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading

from .playlist import Track
from .util import clean_env

_URL_RE = re.compile(r"^(https?://|www\.)", re.I)
_YT_HOST_RE = re.compile(r"(youtube\.com|youtu\.be|music\.youtube\.com)", re.I)


def looks_like_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def yt_dlp_available() -> bool:
    return shutil.which("yt-dlp") is not None


class Searcher:
    """Runs one yt-dlp query at a time; a new query supersedes the old one."""

    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()
        self._generation = 0
        self._proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1
            proc = self._proc
            self._proc = None
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def search_async(self, query: str, callback, limit: int | None = None) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
        thread = threading.Thread(
            target=self._run, args=(query, callback, limit, generation), daemon=True
        )
        thread.start()

    def _run(self, query: str, callback, limit, generation) -> None:
        try:
            tracks, error = self._query(query, limit)
        except Exception as exc:                      # never kill the UI thread
            tracks, error = [], f"search failed: {exc}"
        with self._lock:
            stale = generation != self._generation
        if not stale:
            callback(tracks, error)

    def _query(self, query: str, limit) -> tuple[list, str | None]:
        query = query.strip()
        if not query:
            return [], None
        if not yt_dlp_available():
            return [], "yt-dlp is not installed"

        limit = limit or int(self.config.get("search", "results", 25))
        cmd = ["yt-dlp", "-J", "--flat-playlist", "--no-warnings", "--ignore-errors",
               "--no-progress", "--socket-timeout", "20"]
        cmd += self.config.ytdlp_auth_args()

        if looks_like_url(query):
            if not _YT_HOST_RE.search(query):
                return [], "only YouTube links are supported"
            cmd += ["--playlist-end", str(max(limit, 100)), query]
        else:
            cmd.append(f"ytsearch{limit}:{query}")

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=clean_env(), text=True,
        )
        with self._lock:
            self._proc = proc
        try:
            out, err = proc.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            proc.kill()
            return [], "search timed out"
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None

        if not out.strip():
            return [], self._explain(err) or "no results"

        tracks = []
        for line in out.splitlines():           # --ignore-errors can emit several
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            entries = payload.get("entries")
            if entries is None:
                entries = [payload]
            for entry in entries:
                # Nested playlists (e.g. a channel) arrive one level deeper.
                inner = entry.get("entries") if isinstance(entry, dict) else None
                for item in (inner if inner is not None else [entry]):
                    track = Track.from_entry(item)
                    if track:
                        tracks.append(track)

        if not tracks:
            return [], self._explain(err) or "no results"
        return tracks[:limit] if not looks_like_url(query) else tracks, None

    @staticmethod
    def _explain(stderr: str) -> str | None:
        """Turn yt-dlp's noise into one actionable line."""
        if not stderr:
            return None
        text = stderr.lower()
        if "sign in to confirm" in text or "bot" in text and "confirm" in text:
            return "YouTube wants a signed-in session - press L to sign in"
        if "private video" in text:
            return "private video - press L to sign in"
        if "age" in text and "confirm" in text:
            return "age-restricted - press L to sign in"
        if "could not copy" in text and "cookie" in text:
            return "could not read browser cookies (close the browser and retry)"
        if "unable to download" in text or "urlopen" in text or "timed out" in text:
            return "network error reaching YouTube"
        for line in stderr.splitlines():
            if line.startswith("ERROR:"):
                return line[6:].strip()[:120]
        return None
