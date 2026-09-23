"""Configuration: on-disk JSON with sane defaults and a YouTube auth policy.

Authentication note: yt-dlp cannot log in with a username/password anymore —
Google blocks scripted password sign-in and requires interactive 2FA. The
supported way to be "logged in" is to reuse the session cookies of a browser
you are already signed into. That read happens locally; the cookies only ever
travel to YouTube itself.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .util import config_dir

DEFAULTS = {
    "auth": {
        # "none"       - anonymous (default)
        # "browser"    - reuse cookies from a local browser profile
        # "cookiefile" - reuse a Netscape-format cookies.txt
        "mode": "none",
        "browser": "firefox",
        "profile": "",
        "cookie_file": "",
    },
    "player": {
        "volume": 70,
        "format": "bestaudio/best",
        "seek_step": 5,
        "seek_step_big": 30,
    },
    "search": {
        "results": 25,
    },
    "ui": {
        # "gradient" uses the 256-colour cube, "ansi" inherits your Omarchy
        # terminal theme, "mono" drops colour entirely.
        "palette": "gradient",
        "vis_height": 9,
        "vis_mode": "spectrum",   # spectrum | scope | vu | off
        "bands": 0,               # 0 = fit to terminal width
    },
    "audio": {
        "rate": 44100,
        "chunk": 2048,
        "source": "auto",         # "auto" = follow the default sink's monitor
    },
}

# Browsers yt-dlp can pull cookies from, in the order we suggest them.
KNOWN_BROWSERS = [
    "firefox", "chrome", "chromium", "brave", "edge",
    "opera", "vivaldi", "safari", "whale",
]


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, data: dict, path: Path):
        self.data = data
        self.path = path

    @classmethod
    def load(cls) -> "Config":
        path = config_dir() / "config.json"
        data = dict(DEFAULTS)
        if path.exists():
            try:
                data = _merge(DEFAULTS, json.loads(path.read_text()))
            except (OSError, ValueError):
                # A corrupt config should never stop the player from starting.
                pass
        return cls(data, path)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2) + "\n")
        except OSError:
            pass

    def get(self, section: str, key: str, fallback=None):
        return self.data.get(section, {}).get(key, DEFAULTS.get(section, {}).get(key, fallback))

    def set(self, section: str, key: str, value) -> None:
        self.data.setdefault(section, {})[key] = value

    # --- authentication -------------------------------------------------
    @property
    def auth_mode(self) -> str:
        return self.get("auth", "mode", "none")

    def auth_label(self) -> str:
        mode = self.auth_mode
        if mode == "browser":
            return f"cookies:{self.get('auth', 'browser', 'firefox')}"
        if mode == "cookiefile":
            return "cookies:file"
        return "anonymous"

    def ytdlp_auth_args(self) -> list:
        """Auth flags for a direct yt-dlp invocation."""
        mode = self.auth_mode
        if mode == "browser":
            browser = self.get("auth", "browser", "firefox")
            profile = self.get("auth", "profile", "")
            return ["--cookies-from-browser", f"{browser}:{profile}" if profile else browser]
        if mode == "cookiefile":
            path = self.get("auth", "cookie_file", "")
            if path and Path(path).expanduser().exists():
                return ["--cookies", str(Path(path).expanduser())]
        return []

    def mpv_auth_args(self) -> list:
        """The same auth, expressed as mpv's ytdl_hook raw options."""
        mode = self.auth_mode
        if mode == "browser":
            browser = self.get("auth", "browser", "firefox")
            profile = self.get("auth", "profile", "")
            value = f"{browser}:{profile}" if profile else browser
            return [f"--ytdl-raw-options-append=cookies-from-browser={value}"]
        if mode == "cookiefile":
            path = self.get("auth", "cookie_file", "")
            if path and Path(path).expanduser().exists():
                return [f"--ytdl-raw-options-append=cookies={Path(path).expanduser()}"]
        return []


def detect_browsers() -> list:
    """Browsers that look installed, so the auth picker offers real choices."""
    probes = {
        "firefox": ["firefox", "~/.mozilla/firefox"],
        "chrome": ["google-chrome-stable", "google-chrome", "~/.config/google-chrome"],
        "chromium": ["chromium", "~/.config/chromium"],
        "brave": ["brave", "brave-browser", "~/.config/BraveSoftware"],
        "edge": ["microsoft-edge", "~/.config/microsoft-edge"],
        "vivaldi": ["vivaldi", "~/.config/vivaldi"],
        "opera": ["opera", "~/.config/opera"],
    }
    found = []
    for name, candidates in probes.items():
        for candidate in candidates:
            if candidate.startswith("~"):
                if Path(candidate).expanduser().exists():
                    found.append(name)
                    break
            elif shutil.which(candidate):
                found.append(name)
                break
    return found or ["firefox"]
