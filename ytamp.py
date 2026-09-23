#!/usr/bin/env python3
"""Standalone launcher: `./ytamp.py` or `python3 ytamp.py`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ytamp.__main__ import main   # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
