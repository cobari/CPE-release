"""Provide exhaustive reference matching independent of the production matcher."""

from ._oracle import run
from ._search import search

__all__ = ["run", "search"]
