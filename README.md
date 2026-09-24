# ytamp

**A retro terminal player for YouTube audio — with a real spectrum analyser.**

ytamp searches YouTube, queues tracks and plays them from a TUI that owes its
layout to the skinnable MP3 players of the late nineties. The visualiser is not
decoration driven by a timer: it taps the audio actually leaving your speakers
and runs an FFT over it. When you'd rather watch than listen, one key hands the
track to your browser.

```
 ♫ YTAMP · youtube terminal amp                                                 anonymous · PLAYING
 ▶ Loud Luxury feat. brando - Body (Official Music Video)
   1:24 / 3:40  ·  Armada Music TV  ·  130 kbps  ·  track 1/1                    ▶ WATCH VIDEO (o)

       ▔
 ▔ ▔ ▄ ▅ ▄   ▔                                           ▔ ▂
   █ █ █ █ ▇   ▔ ▔           ▔           ▔       ▔ ▁ ▔ ▅ ▇ █ ▃
 ▄ █ █ █ █ █ █     ▁ ▃       ▃ ▔       ▔   ▄ ▂ ▅ █ █ █ █ █ █ █ ▃ ▁ ▁ ▔
 █ █ █ █ █ █ █     █ █ ▇ ▔ ▇ █ ▇ ▁ ▔ ▁ ▆ ▇ █ █ █ █ █ █ █ █ █ █ █ █ █ █ ▅ ▂ ▆ ▁ ▔ ▅ ▅ ▔ ▔ ▔ ▔ ▔
 █ █ █ █ █ █ █ █ ▇ █ █ █ ▇ █ █ █ █ ▃ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ ▆ █ █ ▄ ▃ ▅ █ ▂ ▔ ▔
 █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ ▅ ▂
 █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●───────────────────────────────────────────────────────────
  VOL ████████████░░░░░░░░░░░░  70%  SHUFFLE  REPEAT:OFF  VIS:SPECTRUM
 RESULTS (25)   QUEUE (1)                                     / search   ↵ play   a queue   ? help
 ▶ 1  Loud Luxury feat. brando - Body (Official Music Video)  Armada Music TV          284M   3:41
   2  Loud Luxury feat. Brando - Body (Official Music Video)  TMRW Music               3.3M   3:13
   3  Loud Luxury - Body (feat. brando)                       MrRevillz                 17M   2:44
 / search   space play/pause   n p track   ← → seek   o watch   ? help   q quit
```

---

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [Usage](#usage)
- [The visualiser](#the-visualiser)
- [Signing in](#signing-in)
- [Configuration](#configuration)
- [Command line](#command-line)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

---

## What it does

- **Search YouTube from the terminal.** Type a query, get titles, channels,
  view counts and durations in about two seconds.
- **Paste links.** A video URL plays it; a playlist or channel URL loads every
  entry into Results, and `A` sends the lot to the queue.
- **A real queue.** Add, reorder, remove, shuffle, repeat one or all. It saves
  on exit and comes back next time.
- **A genuine spectrum analyser.** Log-spaced FFT bands with falling peak
  markers, plus an oscilloscope and stereo VU meters.
- **One key to the video.** `o` opens the current track on youtube.com, for
  when the music video is the point.
- **Mouse support.** Click the seek bar, the volume bar, the tabs, the
  `▶ WATCH VIDEO` button, or a row in the list.
- **Anonymous by default.** No account needed. Optional cookie-based sign-in
  for age-restricted or private material.
- **Streams, never downloads.** Nothing is written to disk but your queue.

## Requirements

| | Package | Why |
|---|---|---|
| **Required** | `mpv` | playback |
| **Required** | `yt-dlp` | search and stream resolution |
| **Required** | Python 3.9+ | `curses` only, from the standard library |
| Recommended | `pipewire-audio` (`pw-cat`) or `pulseaudio-utils` (`parec`) | feeds the visualiser |
| Recommended | `numpy` | the high-resolution analyser |

ytamp runs without the recommended two. Missing `pw-cat`/`parec` leaves the
bars flat; missing `numpy` falls back to a pure-Python FFT that is real, just
coarser at the low end.

Arch / Omarchy:

```bash
sudo pacman -S mpv yt-dlp pipewire-audio python-numpy
```

Debian / Ubuntu:

```bash
sudo apt install mpv yt-dlp pipewire-bin python3-numpy
```

A terminal of at least **80×24** and a UTF-8 locale. 256 colours if you want
the analyser gradient.

## Install

```bash
git clone https://github.com/digonto10602/ytamp.git
cd ytamp
python3 ytamp.py
```

There is nothing to build and no dependencies to install. To get a `ytamp`
command anywhere, drop a launcher on your `PATH`:

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/ytamp <<'EOF'
#!/usr/bin/env bash
exec python3 "$HOME/path/to/ytamp/ytamp.py" "$@"
EOF
chmod +x ~/.local/bin/ytamp
```

If you use conda, prefer an interpreter that has numpy — ytamp already strips
conda's library paths from everything it spawns, so running under a conda
python is safe.

## Quick start

```bash
ytamp                                   # open the player
ytamp boards of canada roygbiv          # open on a search
ytamp 'https://youtube.com/playlist?list=...'   # load a playlist
```

Then: press `/`, type what you want, `Enter`. Move with `j`/`k`, press `Enter`
to play, `a` to queue. Press `?` at any time for the full key list.

## Usage

### Searching

Press `/` and type. Plain words become a YouTube search. Anything starting with
`http` is treated as a link:

| Input | Result |
|---|---|
| `aphex twin windowlicker` | search, newest results first |
| `https://www.youtube.com/watch?v=...` | that single video |
| `https://youtu.be/...` | that single video |
| `https://www.youtube.com/playlist?list=...` | every track in the playlist into Results |
| a channel or mix URL | as many entries as yt-dlp will list |

`Esc` cancels the prompt, `Ctrl-U` clears the line, `Ctrl-W` deletes a word.
Results arrive on a background thread, so the player keeps running and the
analyser keeps moving while a search is in flight.

### Playing and queueing

The interface has two panes, switched with `Tab`:

- **RESULTS** — what your last search returned.
- **QUEUE** — what will actually play.

`Enter` on a result plays it immediately, adding it to the queue if it isn't
there. `a` queues without interrupting what's playing. `Enter` on a queue row
jumps to that track. When a track ends, ytamp advances on its own, honouring
shuffle and repeat.

### Watching the video

Audio-only is the point of a terminal player, right up until it isn't. Press
`o` for the track that's playing, or `O` for whatever the cursor is on, and it
opens on youtube.com in your normal browser. The `▶ WATCH VIDEO` button in the
header does the same thing and is clickable.

### Keys

Press `?` in the app for this list.

**Search and navigation**

| Key | Action |
|---|---|
| `/` | search YouTube, or paste a link |
| `Tab` | switch between Results and Queue |
| `j` `k` or `↑` `↓` | move the cursor |
| `g` `G` | jump to top / bottom |
| `PgUp` `PgDn` | page through the list |

**Playback**

| Key | Action |
|---|---|
| `Enter` | play the selected track |
| `a` | add the selected result to the queue |
| `A` | add every result to the queue |
| `Space` | play / pause |
| `n` `p` | next / previous track |
| `←` `→` | seek 5 seconds |
| `Shift`+`←` `→` | seek 30 seconds |
| `+` `-` | volume up / down |
| `m` | mute |
| `s` | shuffle on / off |
| `r` | repeat: off → all → one |

**Queue**

| Key | Action |
|---|---|
| `d` | remove the selected track |
| `c` | clear the queue |
| `J` `K` | move the selected track down / up |

**Other**

| Key | Action |
|---|---|
| `o` | watch the current track on youtube.com |
| `O` | watch the selected track on youtube.com |
| `v` | cycle visualiser: spectrum → scope → VU → off |
| `L` | sign in with browser cookies, or go anonymous |
| `?` | toggle help |
| `q` | quit (the queue is saved) |

### Mouse

| Action | Effect |
|---|---|
| Click `▶ WATCH VIDEO` | open the current track in the browser |
| Click the seek bar | jump to that position |
| Click the volume bar | set the volume |
| Click `RESULTS` / `QUEUE` | switch pane |
| Click a row | select it; click again to play |
| Scroll wheel | scroll the list |

## The visualiser

Four modes, cycled with `v`:

| Mode | What it shows |
|---|---|
| `spectrum` | log-spaced FFT bands, 40 Hz–14 kHz, with falling peak markers |
| `scope` | an oscilloscope of the waveform |
| `vu` | stereo L/R level meters |
| `off` | hides it and gives the rows to the list |

mpv does not hand out PCM, so ytamp taps the **monitor of your default sink**
and runs an FFT over it. The bars therefore follow the real output, and the tap
re-attaches by itself if you move between laptop speakers, HDMI and Bluetooth
mid-song.

Levels are auto-gained against a slowly decaying reference, so a quiet
Bluetooth stream and a loud analog one both fill the display. The analyser only
runs while ytamp is playing: some sinks, Bluetooth especially, keep a
surprisingly loud idle floor on their monitor, and without that gate the auto
gain would stretch that noise into full-height bars while nothing was playing.

### Bluetooth and audio quality

This one is worth knowing about, because it is a trap.

A plain capture stream makes WirePlumber think an application wants a
microphone, so it switches a Bluetooth headset from A2DP to the headset
profile — **mono, 16 kHz** — and the music audibly collapses. ytamp avoids this
by declaring `stream.capture.sink`, which marks the tap as a sink monitor
rather than a microphone request, and by targeting the sink itself instead of
its `.monitor`.

Because the cost of being wrong is someone's music quietly turning to mono,
ytamp does not simply trust that. On every capture start it records each
Bluetooth card's active profile, checks again a moment later, and if the
profile was downgraded it kills the tap, puts the profile back, and turns the
visualiser off with a message rather than leave your audio degraded.

Capture processes are also started with `PR_SET_PDEATHSIG` so they die with
ytamp. Without it, an orphan left by a crash keeps the headset pinned to the
low quality profile until you hunt the process down.

If a headset ever does get stuck this way — from any application — this fixes
it:

```bash
pactl list cards | grep -A1 bluez        # find the card name
pactl set-card-profile bluez_card.XX_XX_XX_XX_XX_XX a2dp-sink
```

### Tuning

Two constants in `ytamp/audio.py` decide how the bars look, and they are
calibrated as a pair against a real full-range capture of dance material
covering both a quiet section and a drop:

- **the frequency tilt** (`0.50`, about 3 dB/octave) offsets music's natural
  high-frequency roll-off, which measured about 4.8 dB/octave across 38 dB from
  sub-bass to the top octave, while leaving the bass clearly dominant.
- **`FLOOR_DB`** (`30.0`) sets how many dB of range the bar height spans.

If you re-tune them, judge the result by rendering all the visualiser rows, not
one character per band: a level of 0.5 already means half the column is solid.
The target is a mean near 0.4, which keeps air above the bars in quiet passages
while still letting a drop fill the display.

## Signing in

**ytamp is anonymous by default and never asks for a password.** That is
deliberate rather than a missing feature: Google blocks scripted
username/password sign-in and requires interactive 2FA, so yt-dlp dropped
support for it. The supported way to be "logged in" is to reuse the session
cookies of a browser you are already signed into.

Press `L` and pick a browser. ytamp detects which ones you actually have
installed and lists those first. Cookies are read from that browser's local
profile and only ever sent to YouTube — nothing is uploaded anywhere else, and
your password is never involved.

You only need this for age-restricted, private or members-only tracks, or if
YouTube starts demanding a signed-in session.

```bash
ytamp --cookies-from-browser firefox     # chrome, chromium, brave, edge, vivaldi, opera…
ytamp --cookies ~/cookies.txt            # a Netscape-format cookie file
```

Two things worth knowing: Chromium-family browsers lock their cookie database
while running, so close the browser if the read fails; and the choice is
remembered, so `L` → *Anonymous* turns it back off.

## Configuration

`~/.config/ytamp/config.json`, written on exit and created on first run.

| Key | Default | Meaning |
|---|---|---|
| `auth.mode` | `none` | `none`, `browser` or `cookiefile` |
| `auth.browser` | `firefox` | which browser to take cookies from |
| `auth.profile` | `""` | a named browser profile, if you use several |
| `auth.cookie_file` | `""` | path to a `cookies.txt` |
| `player.volume` | `70` | remembered between runs |
| `player.format` | `bestaudio/best` | yt-dlp format selector |
| `player.seek_step` | `5` | seconds for `←` `→` |
| `player.seek_step_big` | `30` | seconds for `Shift`+`←` `→` |
| `search.results` | `25` | results per text search (links are never capped) |
| `ui.palette` | `gradient` | `gradient` (256 colour), `ansi` (inherit your terminal theme), `mono` |
| `ui.vis_height` | `9` | analyser height in rows |
| `ui.vis_mode` | `spectrum` | startup visualiser mode |
| `ui.bands` | `0` | analyser bands; `0` fits the terminal width |
| `audio.rate` | `44100` | capture sample rate |
| `audio.chunk` | `2048` | FFT window; larger is smoother but less responsive |
| `audio.source` | `auto` | pin a sink instead of following the default |

The queue lives in `~/.local/share/ytamp/queue.json`.

## Command line

```
ytamp [query…] [options]

  query                        search terms, or a YouTube link, to open at startup
  --vis {spectrum,scope,vu,off}  visualiser mode for this run
  --palette {gradient,ansi,mono} colour handling for this run
  --cookies-from-browser NAME  sign in using a browser's cookies
  --cookies FILE               sign in using a cookies.txt
  --no-restore                 start with an empty queue
  --version
```

## How it works

| Module | Job |
|---|---|
| `ytamp/ui.py` | the curses interface: layout, drawing, keys, mouse |
| `ytamp/player.py` | headless mpv driven over its JSON IPC socket |
| `ytamp/audio.py` | monitor capture, FFT, auto-gain, peak decay, the Bluetooth guard |
| `ytamp/search.py` | yt-dlp search and link resolution, off the UI thread |
| `ytamp/playlist.py` | the queue: order, shuffle, repeat, persistence |
| `ytamp/theme.py` | colour pairs and the analyser gradient |
| `ytamp/config.py` | JSON config, defaults, browser detection |
| `ytamp/util.py` | formatting, environment hygiene, process lifetime |

mpv runs headless (`--idle --no-video`) and ytamp talks to it over a unix
socket. Properties are *observed* rather than polled, so mpv pushes position,
duration, title and bitrate as they change and the UI just reads a dict. The
whole screen is repainted about 30 times a second, with every cell written each
frame — relying on curses' erase optimisation alone leaves stale glyphs when a
short string overwrites a longer one.

Every subprocess is spawned with a cleaned environment, because a conda
`LD_LIBRARY_PATH` otherwise makes system binaries link against the wrong
libraries.

## Troubleshooting

**The bars never move.** Install `pw-cat` (`pipewire-audio`) or `parec`
(`pulseaudio-utils`). If they are present, check that `audio.source` isn't
pinned to a device you no longer use. The analyser is also deliberately still
unless playback is actually running.

**"visualiser off: it was degrading Bluetooth audio quality".** The safety
guard fired: opening the tap downgraded your headset, so ytamp shut the tap
down and restored the profile. Your audio is fine; the analyser stays off for
that session.

**Search returns "YouTube wants a signed-in session".** Press `L` and pick a
browser you're signed into.

**"could not read browser cookies".** Chromium-family browsers lock their
cookie database. Close the browser and try again.

**Playback stops immediately on one track.** Usually age-restricted or private.
Sign in with `L`, or press `o` to watch it in the browser instead.

**The layout looks cramped.** ytamp wants 80×24. Below that it drops the
analyser first, then the uploader and view-count columns.

**Boxes instead of block characters.** Your terminal or font lacks the Unicode
block glyphs, or the locale isn't UTF-8. Try `--palette mono` and a font like
JetBrains Mono or Fira Code.

## Limitations

- Audio only in the terminal. Video means the browser, by design.
- ytamp streams; it does not download. There is no offline mode.
- Without numpy the fallback FFT uses 43 Hz bins, so bands below about 90 Hz
  collapse together and the low end reads as a plateau.
- The analyser taps the default sink's monitor, so on the rare setup where
  something else is playing through the same sink, that audio shows up too.
- YouTube changes things. If search or playback breaks, update `yt-dlp` first.

## License

MIT. See [LICENSE](LICENSE).
