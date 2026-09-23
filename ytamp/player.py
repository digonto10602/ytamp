"""mpv playback over its JSON IPC socket.

mpv runs headless (--no-video --idle) and we talk to it through a unix
socket: commands out, observed properties and events back. Properties are
pushed by mpv rather than polled, so the UI just reads a dict.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time

from .util import clean_env, die_with_parent, runtime_dir

# Properties mpv pushes to us whenever they change.
OBSERVED = [
    "time-pos", "duration", "pause", "media-title", "volume", "mute",
    "core-idle", "audio-bitrate", "paused-for-cache", "cache-buffering-state",
    "demuxer-cache-time", "eof-reached", "audio-codec-name", "filename",
]


class MpvPlayer:
    def __init__(self, config, on_end=None, on_log=None):
        self.config = config
        self.on_end = on_end            # called when a track finishes naturally
        self.on_log = on_log            # called with a human-readable status line
        self.proc: subprocess.Popen | None = None
        self.sock: socket.socket | None = None
        self.sock_path = str(runtime_dir() / f"ytamp-{os.getpid()}.sock")
        self.state: dict = {}
        self.lock = threading.Lock()
        self.running = False
        self._req_id = 0
        self._loading = False           # suppress end-file from our own load()
        self._last_load = 0.0

    # --- lifecycle ------------------------------------------------------
    @staticmethod
    def available() -> bool:
        return shutil.which("mpv") is not None

    def start(self) -> bool:
        if not self.available():
            self._log("mpv is not installed")
            return False
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass

        volume = int(self.config.get("player", "volume", 70))
        cmd = [
            "mpv", "--idle=yes", "--no-video", "--no-terminal", "--really-quiet",
            "--audio-display=no", "--gapless-audio=yes",
            f"--input-ipc-server={self.sock_path}",
            f"--ytdl-format={self.config.get('player', 'format', 'bestaudio/best')}",
            f"--volume={volume}",
            "--cache=yes", "--demuxer-max-bytes=64MiB", "--demuxer-readahead-secs=30",
        ]
        if shutil.which("yt-dlp"):
            cmd.append("--script-opts=ytdl_hook-ytdl_path=/usr/bin/yt-dlp")
        cmd += self.config.mpv_auth_args()

        try:
            self.proc = subprocess.Popen(
                cmd, env=clean_env(), preexec_fn=die_with_parent,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self._log(f"could not start mpv: {exc}")
            return False

        # mpv creates the socket a moment after exec.
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self._log("mpv exited during startup")
                return False
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self.sock_path)
                self.sock = sock
                break
            except OSError:
                time.sleep(0.05)
        if not self.sock:
            self._log("timed out connecting to mpv")
            return False

        self.running = True
        threading.Thread(target=self._reader, daemon=True).start()
        for index, prop in enumerate(OBSERVED, start=1):
            self._send({"command": ["observe_property", index, prop]})
        self.state["volume"] = volume
        return True

    def stop(self) -> None:
        self.running = False
        try:
            if self.sock:
                self._send({"command": ["quit"]})
                time.sleep(0.1)
                self.sock.close()
        except OSError:
            pass
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.proc.kill()
                except OSError:
                    pass
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass

    # --- IPC plumbing ---------------------------------------------------
    def _log(self, message: str) -> None:
        if self.on_log:
            self.on_log(message)

    def _send(self, payload: dict) -> None:
        if not self.sock:
            return
        self._req_id += 1
        payload.setdefault("request_id", self._req_id)
        try:
            self.sock.sendall((json.dumps(payload) + "\n").encode())
        except OSError:
            self.running = False

    def _reader(self) -> None:
        buffer = b""
        while self.running and self.sock:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    self._handle(json.loads(line.decode("utf-8", "replace")))
                except ValueError:
                    continue
        self.running = False

    def _handle(self, msg: dict) -> None:
        event = msg.get("event")
        if event == "property-change":
            with self.lock:
                self.state[msg.get("name")] = msg.get("data")
            return
        if event == "start-file":
            self._loading = False
            with self.lock:
                self.state["eof-reached"] = False
            return
        if event == "end-file":
            reason = msg.get("reason")
            if reason == "error":
                self._log("playback error - the stream may need cookies (A)")
            # "stop"/"redirect" are our own loadfile calls; only a natural
            # end-of-stream should advance the queue.
            if reason == "eof" and not self._loading and self.on_end:
                if time.time() - self._last_load > 1.0:
                    self.on_end()
            return

    # --- transport ------------------------------------------------------
    def load(self, url: str) -> None:
        self._loading = True
        self._last_load = time.time()
        with self.lock:
            self.state.update({"time-pos": 0, "duration": None, "pause": False,
                               "media-title": None, "eof-reached": False})
        self._send({"command": ["loadfile", url, "replace"]})
        self._send({"command": ["set_property", "pause", False]})

    def set_pause(self, value: bool) -> None:
        self._send({"command": ["set_property", "pause", bool(value)]})

    def toggle_pause(self) -> None:
        self.set_pause(not self.get("pause", False))

    def seek(self, delta: float) -> None:
        self._send({"command": ["seek", delta, "relative"]})

    def seek_percent(self, percent: float) -> None:
        self._send({"command": ["seek", max(0.0, min(100.0, percent)), "absolute-percent"]})

    def set_volume(self, volume: int) -> int:
        volume = max(0, min(130, int(volume)))
        self._send({"command": ["set_property", "volume", volume]})
        with self.lock:
            self.state["volume"] = volume
        return volume

    def toggle_mute(self) -> None:
        self._send({"command": ["set_property", "mute", not self.get("mute", False)]})

    def stop_playback(self) -> None:
        self._loading = True
        self._send({"command": ["stop"]})

    # --- state ----------------------------------------------------------
    def get(self, key: str, fallback=None):
        with self.lock:
            value = self.state.get(key, fallback)
        return fallback if value is None else value

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def status(self) -> str:
        """One short word for the transport state, Winamp-style."""
        if not self.running:
            return "DEAD"
        if self.get("paused-for-cache", False):
            return "BUFFER"
        if self.get("pause", False):
            return "PAUSED"
        if self.get("core-idle", True):
            return "STOPPED"
        return "PLAYING"
