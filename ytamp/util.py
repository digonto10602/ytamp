"""Small shared helpers: environment hygiene, formatting, paths."""
from __future__ import annotations

import ctypes
import os
import shutil
import signal
import subprocess
from pathlib import Path

# Conda / toolchain variables leak into child processes and make system
# binaries (mpv, yt-dlp, pw-cat) link against the wrong libraries. Every
# subprocess we spawn goes through clean_env().
_POISON = ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH")


def clean_env() -> dict:
    env = dict(os.environ)
    for key in _POISON:
        env.pop(key, None)
    return env


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "ytamp"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "ytamp"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(base)


def fmt_time(seconds) -> str:
    """Format seconds as M:SS, or H:MM:SS past an hour. '--:--' when unknown."""
    if seconds is None:
        return "--:--"
    try:
        total = int(max(0, float(seconds)))
    except (TypeError, ValueError):
        return "--:--"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def fmt_count(n) -> str:
    """Compact view counts: 1.2M, 618M, 4.1K."""
    if not n:
        return ""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= limit:
            value = n / limit
            return f"{value:.1f}{suffix}" if value < 10 else f"{value:.0f}{suffix}"
    return str(n)


def ellipsize(text: str, width: int) -> str:
    """Clip text to width, marking truncation with a single ellipsis."""
    text = "" if text is None else str(text)
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def open_in_browser(url: str) -> bool:
    """Hand a URL to the desktop's browser. Never blocks the UI."""
    for cmd in (["xdg-open", url], [os.environ.get("BROWSER", ""), url]):
        if not cmd[0] or not shutil.which(cmd[0]):
            continue
        try:
            subprocess.Popen(
                cmd,
                env=clean_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            continue
    return False


# Load libc once, at import time and before any threads exist, so the
# preexec_fn below only has to make a single already-resolved syscall.
try:
    _LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
except OSError:
    _LIBC = None

_PR_SET_PDEATHSIG = 1


def die_with_parent() -> None:
    """preexec_fn: ask the kernel to SIGTERM this child if ytamp dies.

    Without this an orphaned capture process keeps running after a crash or a
    kill -9, and on Bluetooth that silently pins the headset to its low quality
    headset profile until the user notices and hunts the process down.
    """
    if _LIBC is not None:
        try:
            _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
        except Exception:
            pass
