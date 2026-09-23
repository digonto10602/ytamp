"""Entry point: dependency checks, argument parsing, curses bootstrap."""
from __future__ import annotations

import argparse
import curses
import locale
import shutil
import signal
import sys
import traceback

from . import __version__
from .config import Config
from .ui import App


def check_dependencies() -> list:
    """Hard requirements first, then the optional analyser stack."""
    missing = []
    if not shutil.which("mpv"):
        missing.append(("mpv", "playback", "sudo pacman -S mpv"))
    if not shutil.which("yt-dlp"):
        missing.append(("yt-dlp", "search and streaming", "sudo pacman -S yt-dlp"))
    return missing


def warn_optional() -> list:
    warnings = []
    if not (shutil.which("pw-cat") or shutil.which("parec")):
        warnings.append("pw-cat/parec not found - the visualiser will stay flat "
                        "(sudo pacman -S pipewire-audio)")
    try:
        import numpy  # noqa: F401
    except ImportError:
        warnings.append("numpy not found - using the low-resolution visualiser "
                        "(pip install numpy)")
    return warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ytamp",
        description="Search YouTube and play it in a retro terminal player.",
    )
    parser.add_argument("query", nargs="*", help="search terms or a YouTube link to open at startup")
    parser.add_argument("--version", action="version", version=f"ytamp {__version__}")
    parser.add_argument("--vis", choices=("spectrum", "scope", "vu", "off"),
                        help="visualiser mode for this run")
    parser.add_argument("--palette", choices=("gradient", "ansi", "mono"),
                        help="gradient (256 colour), ansi (inherit terminal theme), or mono")
    parser.add_argument("--cookies-from-browser", metavar="BROWSER",
                        help="sign in by reusing cookies from a local browser profile")
    parser.add_argument("--cookies", metavar="FILE", help="sign in with a cookies.txt file")
    parser.add_argument("--no-restore", action="store_true", help="start with an empty queue")
    args = parser.parse_args(argv)

    missing = check_dependencies()
    if missing:
        print("ytamp needs the following before it can run:\n", file=sys.stderr)
        for name, why, how in missing:
            print(f"  {name:<8} ({why})   ->  {how}", file=sys.stderr)
        return 1

    locale.setlocale(locale.LC_ALL, "")      # block-drawing characters need this

    config = Config.load()
    if args.vis:
        config.set("ui", "vis_mode", args.vis)
    if args.palette:
        config.set("ui", "palette", args.palette)
    if args.cookies_from_browser:
        config.set("auth", "mode", "browser")
        config.set("auth", "browser", args.cookies_from_browser)
    elif args.cookies:
        config.set("auth", "mode", "cookiefile")
        config.set("auth", "cookie_file", args.cookies)

    for warning in warn_optional():
        print(f"note: {warning}", file=sys.stderr)

    app_holder = {}

    def terminate(_signum, _frame):
        # Shut down through the normal path so mpv and the capture process are
        # stopped rather than left holding an audio device.
        app = app_holder.get("app")
        if app:
            app.running = False
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, terminate)
        except (ValueError, OSError):
            pass

    def bootstrap(stdscr):
        app = App(stdscr, config)
        app_holder["app"] = app
        app.restore = not args.no_restore
        if args.query:
            app.start_search(" ".join(args.query))
        app.run()

    try:
        curses.wrapper(bootstrap)
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        print(f"terminal error: {exc}\n"
              f"ytamp needs a terminal at least 60x12.", file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        app = app_holder.get("app")
        if app:
            try:
                app.shutdown()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
