"""Compatibility entry point: both import paths use the same implementation."""

from . import create_app

__all__ = ["create_app"]
