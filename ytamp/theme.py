"""Colour handling for the TUI.

Three palettes: "gradient" (256-colour analyser ramp), "ansi" (only the 16
terminal colours, so it inherits your Omarchy theme) and "mono".
"""
from __future__ import annotations

import curses

# Roles mapped to colour-pair numbers.
P_TITLE, P_ACCENT, P_DIM, P_SEL, P_PLAY, P_WARN, P_BTN, P_BAR, P_TAB, P_OK = range(1, 11)
SPECTRUM_BASE = 20

# Bottom-to-top analyser ramp: green through yellow into red.
GRADIENT_256 = [46, 82, 118, 154, 190, 226, 220, 214, 208, 202, 196]
GRADIENT_ANSI = [curses.COLOR_GREEN] * 4 + [curses.COLOR_YELLOW] * 4 + [curses.COLOR_RED] * 3


class Theme:
    def __init__(self, mode: str = "gradient"):
        self.mode = mode
        self.has_color = False
        self.ramp: list[int] = []

    def setup(self) -> None:
        try:
            curses.start_color()
            curses.use_default_colors()
            self.has_color = curses.has_colors()
        except curses.error:
            self.has_color = False
        if not self.has_color or self.mode == "mono":
            self.mode = "mono"
            self.ramp = [0]
            return

        def pair(num, fg, bg=-1):
            try:
                curses.init_pair(num, fg, bg)
            except curses.error:
                pass

        pair(P_TITLE, curses.COLOR_BLACK, curses.COLOR_CYAN)
        pair(P_ACCENT, curses.COLOR_CYAN)
        pair(P_DIM, curses.COLOR_WHITE)
        pair(P_SEL, curses.COLOR_BLACK, curses.COLOR_CYAN)
        pair(P_PLAY, curses.COLOR_GREEN)
        pair(P_WARN, curses.COLOR_YELLOW)
        pair(P_BTN, curses.COLOR_BLACK, curses.COLOR_GREEN)
        pair(P_BAR, curses.COLOR_CYAN)
        pair(P_TAB, curses.COLOR_BLACK, curses.COLOR_WHITE)
        pair(P_OK, curses.COLOR_GREEN)

        colors = GRADIENT_256 if (self.mode == "gradient" and curses.COLORS >= 256) else GRADIENT_ANSI
        self.ramp = []
        for offset, color in enumerate(colors):
            num = SPECTRUM_BASE + offset
            pair(num, color)
            self.ramp.append(num)

    def attr(self, role: int, bold: bool = False, dim: bool = False) -> int:
        if self.mode == "mono":
            base = curses.A_REVERSE if role in (P_TITLE, P_SEL, P_TAB, P_BTN) else curses.A_NORMAL
        else:
            base = curses.color_pair(role)
        if bold:
            base |= curses.A_BOLD
        if dim:
            base |= curses.A_DIM
        return base

    def spectrum_attr(self, fraction: float) -> int:
        """Colour for a bar cell at `fraction` of full height."""
        if self.mode == "mono" or not self.ramp:
            return curses.A_NORMAL
        index = int(max(0.0, min(0.999, fraction)) * len(self.ramp))
        return curses.color_pair(self.ramp[min(index, len(self.ramp) - 1)])
