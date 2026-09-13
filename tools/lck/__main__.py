"""Stable module entry point for the Local Control Kernel."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
