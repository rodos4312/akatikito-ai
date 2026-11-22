"""Akatikito — αυτόνομος βοηθός προγραμματισμού."""

APP_NAME = "Akatikito"
__version__ = "0.1.0"

from .agent import Akatikito  # noqa: E402

__all__ = ["APP_NAME", "__version__", "Akatikito"]
