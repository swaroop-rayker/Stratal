"""Stratal: a local, end-to-end ML lifecycle platform."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("stratal")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"

__all__ = ["__version__"]
