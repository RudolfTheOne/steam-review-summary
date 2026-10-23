#!/usr/bin/env python3
"""Run without installing: python3 steamsum.py <game link> [options]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from steamsum.__main__ import main  # noqa: E402

main()
