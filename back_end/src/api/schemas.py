"""Compatibility view of the canonical API module; no independent state."""

from importlib import import_module


def __getattr__(name):
    return getattr(import_module("src.api"), name)
