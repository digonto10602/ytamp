"""Real-time spectrum analysis of what is actually coming out of the speakers.

mpv does not hand us PCM, so we tap the default sink's monitor with pw-cat
(PipeWire) or parec (PulseAudio) and run an FFT over it. Because the tap
follows the *default sink*, it keeps working when you switch between laptop
speakers, HDMI and Bluetooth mid-song.

One sharp edge drove the design here. A plain capture stream makes
WirePlumber think an application wants a microphone, so it switches a
Bluetooth headset from A2DP to the headset profile - mono, 16 kHz - and the
music audibly collapses. Declaring "stream.capture.sink" marks the stream as
a sink monitor tap instead, which leaves the profile alone. _guard_bluetooth
then verifies that at runtime and disables the analyser rather than let it
quietly degrade someone's audio.

Levels are auto-gained: a quiet Bluetooth stream and a loud analog one both
fill the bars, which matters because absolute monitor levels vary wildly.
"""
from __future__ import annotations

import cmath
import json
import math
import os
import select
import shutil
import subprocess
import threading
import time

from .util import clean_env, die_with_parent

try:
    import numpy as np
except ImportError:                       # graceful fallback, see _tick_simple
    np = None

DEFAULT_BANDS = 48
# Calibrated as a pair against a real 45 s A2DP capture of dance material
# covering both a quiet section and a drop. That signal falls about
# 4.8 dB/octave across 38 dB from sub-bass to the top octave, so the tilt
# (0.50, about 3 dB/octave) offsets most of that roll-off while leaving the
# bass clearly dominant.
#
# Judge any change by rendering the full VIS_H rows, not one glyph per band:
# level 0.5 already means half the column is solid. The target is a mean near
# 0.4, which keeps air above the bars in quiet passages while still letting a
# drop fill the display. Widening the floor parks everything in a solid block;
# narrowing it flattens quiet passages.
SILENCE_FLOOR = 6e-4   # peak amplitude below this counts as silence
FLOOR_DB = 30.0        # dynamic range shown, in dB below the running reference


def default_sink() -> str | None:
    """Name of the current default sink (not its .monitor)."""
    if not shutil.which("pactl"):
        return None
    try:
        sink = subprocess.run(
            ["pactl", "get-default-sink"], capture_output=True, text=True,
            env=clean_env(), timeout=3,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not sink or sink == "@DEFAULT_SINK@":
        return None
    return sink


def bluetooth_profiles() -> dict:
    """Active profile of every Bluetooth card, keyed by card name."""
    if not shutil.which("pactl"):
        return {}
    try:
        out = subprocess.run(
            ["pactl", "--format=json", "list", "cards"], capture_output=True,
            text=True, env=clean_env(), timeout=3,
        ).stdout
        cards = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    return {c.get("name"): c.get("active_profile") for c in cards
            if isinstance(c, dict) and "bluez" in str(c.get("name", ""))}


def _is_headset_profile(profile) -> bool:
    return any(hint in str(profile).lower()
               for hint in ("headset", "handsfree", "hfp", "hsp"))


class SpectrumTap:
    """Background capture + FFT. Read the results with snapshot()."""

    def __init__(self, config):
        self.config = config
        self.rate = int(config.get("audio", "rate", 44100))
        self.chunk = int(config.get("audio", "chunk", 2048))
        self.bands = DEFAULT_BANDS
        self.levels = [0.0] * self.bands
        self.peaks = [0.0] * self.bands
        self._peak_vel = [0.0] * self.bands
        self.scope = [0.0] * 256
        self.vu = (0.0, 0.0)
        self.lock = threading.Lock()
        self.running = False
        self.active = True      # False -> decay instead of analyse
        self.disabled = False   # set if capture was found to harm audio quality
        self.error: str | None = None
        self.source: str | None = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._buf = bytearray()
        self._ref = 1e-3        # reference for FFT magnitudes
        self._ref_t = 1e-2      # reference for raw sample amplitude
        self._window = None
        self._weights = None
        self._edges = None
        self._pure_n = 0        # pure-Python FFT tables (numpy-free fallback)

    # --- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        self._kill()

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass

    def set_active(self, active: bool) -> None:
        """Analyse only while our player is actually producing sound.

        Some sinks (Bluetooth especially) keep a surprisingly loud idle floor
        on their monitor, which the auto-gain would otherwise stretch into
        full-height bars while nothing is playing.
        """
        self.active = bool(active)

    def set_bands(self, count: int) -> None:
        """Re-fit the analyser to the terminal width."""
        count = max(8, min(200, int(count)))
        if count == self.bands:
            return
        with self.lock:
            self.bands = count
            self.levels = [0.0] * count
            self.peaks = [0.0] * count
            self._peak_vel = [0.0] * count
            self._edges = None

    # --- capture --------------------------------------------------------
    def _spawn(self) -> subprocess.Popen | None:
        configured = self.config.get("audio", "source", "auto")
        target = configured if configured and configured != "auto" else default_sink()
        if not target:
            self.error = "no audio output found"
            return None
        # Accept a ".monitor" name in the config, but target the sink itself:
        # stream.capture.sink is what tells PipeWire we want the monitor.
        sink = target[: -len(".monitor")] if target.endswith(".monitor") else target
        self.source = sink

        if shutil.which("pw-cat"):
            cmd = ["pw-cat", "--record", "--raw",
                   "-P", "stream.capture.sink=true",
                   "--target", sink,
                   "--format", "f32", "--rate", str(self.rate), "--channels", "2", "-"]
            env = clean_env()
        elif shutil.which("parec"):
            cmd = ["parec", "--device", f"{sink}.monitor", "--format=float32le",
                   f"--rate={self.rate}", "--channels=2", "--latency-msec=40"]
            env = clean_env()
            env["PULSE_PROP"] = "stream.capture.sink=true"
        else:
            self.error = "install pipewire-audio (pw-cat) or pulseaudio-utils (parec)"
            return None

        before = bluetooth_profiles()
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, bufsize=0, preexec_fn=die_with_parent,
            )
        except OSError as exc:
            self.error = f"capture failed: {exc}"
            return None

        if not self._guard_bluetooth(proc, before):
            return None
        self.error = None
        return proc

    def _guard_bluetooth(self, proc, before: dict) -> bool:
        """Abort the tap if opening it downgraded a Bluetooth headset.

        stream.capture.sink should prevent this, but the cost of being wrong is
        a user whose music quietly turns to mono until they work out why, so we
        check instead of trusting it.
        """
        if not before:
            return True
        time.sleep(1.2)                     # give the policy time to react
        after = bluetooth_profiles()
        for card, was in before.items():
            now = after.get(card)
            if now and now != was and _is_headset_profile(now) and not _is_headset_profile(was):
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        proc.kill()
                    except OSError:
                        pass
                try:
                    subprocess.run(["pactl", "set-card-profile", card, was],
                                   env=clean_env(), timeout=5,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.SubprocessError):
                    pass
                self.disabled = True
                self.error = "visualiser off: it was degrading Bluetooth audio quality"
                return False
        return True

    def _run(self) -> None:
        frame_bytes = self.chunk * 2 * 4          # frames * stereo * float32
        last_source_check = 0.0
        while self.running:
            if self.disabled:
                self._decay(0.25)
                time.sleep(0.5)
                continue
            if self._proc is None or self._proc.poll() is not None:
                self._kill()
                self._proc = self._spawn()
                if self._proc is None:
                    self._decay(0.25)
                    time.sleep(2.0)
                    continue
                self._buf.clear()

            # Follow the default sink if the user switches output devices.
            now = time.time()
            if now - last_source_check > 3.0:
                last_source_check = now
                if self.config.get("audio", "source", "auto") == "auto":
                    current = default_sink()
                    if current and current != self.source:
                        self._kill()
                        continue

            fd = self._proc.stdout.fileno()
            try:
                ready, _, _ = select.select([fd], [], [], 0.05)
            except (OSError, ValueError):
                self._kill()
                continue
            if not ready:
                # Sink suspended or silent: let the bars fall instead of freezing.
                self._decay(0.12)
                continue
            try:
                data = os.read(fd, 65536)
            except OSError:
                self._kill()
                continue
            if not data:
                self._kill()
                continue

            self._buf += data
            while len(self._buf) >= frame_bytes:
                block = bytes(self._buf[:frame_bytes])
                del self._buf[:frame_bytes]
                self._process(block)
        self._kill()

    # --- analysis -------------------------------------------------------
    def _prepare(self) -> None:
        """Cache the window and the log-spaced band edges for this size."""
        n = self.chunk
        self._window = np.hanning(n).astype(np.float32)
        freqs = np.fft.rfftfreq(n, 1.0 / self.rate)
        low, high = 40.0, min(14000.0, self.rate / 2 - 1)
        edges = np.geomspace(low, high, self.bands + 1)
        self._edges = np.clip(np.searchsorted(freqs, edges), 1, len(freqs) - 1)
        # Music falls off roughly 1/f, so tilt the high end up to keep the
        # right-hand bars alive instead of permanently flat.
        centres = np.sqrt(edges[:-1] * edges[1:])
        self._weights = (centres / 200.0) ** 0.50

    def _process(self, block: bytes) -> None:
        if not self.active:
            self._decay(0.10)
            return
        if np is None:
            self._process_simple(block)
            return
        samples = np.frombuffer(block, dtype=np.float32)
        if samples.size < self.chunk * 2:
            return
        stereo = samples.reshape(-1, 2)
        left, right = stereo[:, 0], stereo[:, 1]
        mono = (left + right) * 0.5

        # A silent monitor still carries a dither floor. Without this gate the
        # auto-gain would happily amplify that noise into full-height bars.
        peak_amp = float(np.abs(mono).max())
        if peak_amp < SILENCE_FLOOR:
            self._decay(0.10)
            return

        with self.lock:
            bands = self.bands
        if self._edges is None or len(self._edges) != bands + 1 or self._window is None:
            self._prepare()

        spectrum = np.abs(np.fft.rfft(mono * self._window))
        power = spectrum * spectrum

        raw = np.empty(bands, dtype=np.float64)
        for i in range(bands):
            start, end = self._edges[i], self._edges[i + 1]
            if end <= start:
                end = start + 1
            raw[i] = np.sqrt(power[start:end].mean())
        raw *= self._weights

        # Auto gain: track a slowly-decaying reference so quiet sources still
        # fill the display, with a floor so silence does not amplify noise.
        loudest = float(raw.max())
        self._ref = max(loudest, self._ref * 0.998)
        reference = max(self._ref, 2e-4)

        norm = raw / reference
        db = 20.0 * np.log10(norm + 1e-7)
        target = np.clip((db + FLOOR_DB) / FLOOR_DB, 0.0, 1.0)

        # Neighbour smoothing keeps the bars reading as a curve, not a comb.
        smooth = target.copy()
        if bands > 2:
            smooth[1:-1] = target[1:-1] * 0.62 + (target[:-2] + target[2:]) * 0.19

        rms_l = float(np.sqrt(np.mean(left * left)))
        rms_r = float(np.sqrt(np.mean(right * right)))
        self._ref_t = max(peak_amp, self._ref_t * 0.996)
        ref_t = max(self._ref_t, 1e-4)
        scope_src = mono[:: max(1, len(mono) // 256)][:256]
        scope = np.clip(scope_src / ref_t * 2.5, -1.0, 1.0)

        with self.lock:
            for i in range(min(bands, len(smooth))):
                value = float(smooth[i])
                old = self.levels[i]
                # Fast attack, slow release - the classic analyser feel.
                self.levels[i] = old + (value - old) * (0.70 if value > old else 0.22)
                if self.levels[i] >= self.peaks[i]:
                    self.peaks[i] = self.levels[i]
                    self._peak_vel[i] = 0.0
                else:
                    self._peak_vel[i] += 0.0035
                    self.peaks[i] = max(self.levels[i], self.peaks[i] - self._peak_vel[i])
            self.scope = [float(v) for v in scope]
            self.vu = (min(1.0, rms_l / ref_t * 2.2), min(1.0, rms_r / ref_t * 2.2))

    # --- numpy-free fallback --------------------------------------------
    PURE_N = 1024      # a 1024-point FFT in pure Python costs ~5k butterflies

    def _prepare_pure(self, bands: int) -> None:
        """Precompute bit-reversal, twiddles, window and band edges."""
        n = self.PURE_N
        bits = n.bit_length() - 1
        rev = [0] * n
        for i in range(n):
            r, x = 0, i
            for _ in range(bits):
                r = (r << 1) | (x & 1)
                x >>= 1
            rev[i] = r
        self._pure_rev = rev
        self._pure_tw = []
        size = 2
        while size <= n:
            half = size // 2
            self._pure_tw.append([cmath.exp(-2j * cmath.pi * k / size) for k in range(half)])
            size *= 2
        self._pure_win = [0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]

        bin_hz = self.rate / n
        low, high = 40.0, min(14000.0, self.rate / 2 - 1)
        ratio = (high / low) ** (1.0 / bands)
        edges, freq = [], low
        for _ in range(bands + 1):
            edges.append(max(1, min(n // 2 - 1, int(freq / bin_hz))))
            freq *= ratio
        self._pure_edges = edges
        self._pure_weights = [
            ((math.sqrt(max(1e-9, edges[i] * edges[i + 1])) * bin_hz) / 200.0) ** 0.50
            for i in range(bands)
        ]
        self._pure_n = n
        self._pure_bands = bands

    def _fft_pure(self, samples):
        """Iterative radix-2 Cooley-Tukey over a real signal."""
        n = self.PURE_N
        rev = self._pure_rev
        buf = [complex(samples[rev[i]], 0.0) for i in range(n)]
        size, stage = 2, 0
        while size <= n:
            half, tw = size // 2, self._pure_tw[stage]
            for start in range(0, n, size):
                for k in range(half):
                    a = start + k
                    b = a + half
                    upper = buf[a]
                    lower = buf[b] * tw[k]
                    buf[a] = upper + lower
                    buf[b] = upper - lower
            size *= 2
            stage += 1
        return buf

    def _process_simple(self, block: bytes) -> None:
        import array

        samples = array.array("f")
        samples.frombytes(block)
        mono = [(samples[i] + samples[i + 1]) * 0.5 for i in range(0, len(samples) - 1, 2)]
        if len(mono) < self.PURE_N:
            return
        if max(abs(v) for v in mono) < SILENCE_FLOOR:
            self._decay(0.10)
            return

        with self.lock:
            bands = self.bands
        if self._pure_n != self.PURE_N or self._pure_bands != bands:
            self._prepare_pure(bands)

        window = self._pure_win
        windowed = [mono[i] * window[i] for i in range(self.PURE_N)]
        spectrum = self._fft_pure(windowed)

        edges, weights = self._pure_edges, self._pure_weights
        raw = []
        for i in range(bands):
            start, end = edges[i], edges[i + 1]
            if end <= start:
                end = start + 1
            total = 0.0
            for bin_index in range(start, end):
                value = spectrum[bin_index]
                total += value.real * value.real + value.imag * value.imag
            raw.append(math.sqrt(total / (end - start)) * weights[i])

        loudest = max(raw)
        self._ref = max(loudest, self._ref * 0.998)
        reference = max(self._ref, 2e-4)

        peak_amp = max(abs(v) for v in mono)
        self._ref_t = max(peak_amp, self._ref_t * 0.996)
        ref_t = max(self._ref_t, 1e-4)

        targets = []
        for value in raw:
            db = 20.0 * math.log10(value / reference + 1e-7)
            targets.append(max(0.0, min(1.0, (db + FLOOR_DB) / FLOOR_DB)))

        step = max(1, len(mono) // 256)
        rms = math.sqrt(sum(v * v for v in mono) / len(mono))

        with self.lock:
            for i, value in enumerate(targets):
                old = self.levels[i]
                self.levels[i] = old + (value - old) * (0.70 if value > old else 0.22)
                if self.levels[i] >= self.peaks[i]:
                    self.peaks[i] = self.levels[i]
                    self._peak_vel[i] = 0.0
                else:
                    self._peak_vel[i] += 0.0035
                    self.peaks[i] = max(self.levels[i], self.peaks[i] - self._peak_vel[i])
            self.scope = [max(-1.0, min(1.0, v / ref_t * 2.5)) for v in mono[::step][:256]]
            level = min(1.0, rms / ref_t * 2.2)
            self.vu = (level, level)

    def _decay(self, amount: float) -> None:
        with self.lock:
            for i in range(len(self.levels)):
                self.levels[i] = max(0.0, self.levels[i] - amount)
                self._peak_vel[i] += 0.004
                self.peaks[i] = max(self.levels[i], self.peaks[i] - self._peak_vel[i])
            self.vu = (max(0.0, self.vu[0] - amount), max(0.0, self.vu[1] - amount))

    def snapshot(self) -> tuple:
        with self.lock:
            return list(self.levels), list(self.peaks), list(self.scope), self.vu
