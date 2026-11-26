"""Akatikito — αυτόνομος βοηθός προγραμματισμού."""

APP_NAME = "Akatikito"
__version__ = "2.6.1"

from .agent import Akatikito  # noqa: E402

__all__ = ["APP_NAME", "__version__", "Akatikito"]
