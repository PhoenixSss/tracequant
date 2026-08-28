#!/usr/bin/env python3
"""Stable CLI facade for the Local Control Kernel.

Implementation is responsibility-owned under :mod:`lck_core`; keep this file
thin so CLI callers never depend on internal module layout.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ != "__main__" or not any(p.endswith("agent_workflow") for p in sys.path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from lck_core.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
