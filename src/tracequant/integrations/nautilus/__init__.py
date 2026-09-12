"""Minimal ownership seam for the installed NautilusTrader distribution."""

from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from types import ModuleType
from typing import Final

DISTRIBUTION_NAME: Final = "nautilus-trader"
EXPECTED_VERSION: Final = "2.0.0rc4"
UPSTREAM_RELEASE_IDENTITY: Final = "2.0.0rc4+a0400251110653b6d8ae6a9b5b89c4543fa85a2d"


def distribution_version() -> str:
    """Return the explicitly requested installed distribution version."""
    return version(DISTRIBUTION_NAME)


def import_package() -> ModuleType:
    """Import the external namespace only when a caller requests it."""
    return import_module("nautilus_trader")


def package_origin() -> Path:
    """Resolve the installed package origin or fail if it has no file identity."""
    location = getattr(import_package(), "__file__", None)
    if not isinstance(location, str):
        raise RuntimeError("nautilus_trader has no concrete module origin")
    return Path(location).resolve()
