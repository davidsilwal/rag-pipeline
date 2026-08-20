#!/usr/bin/env python3
"""apps/control_api/routers/__init__.py — Router package marker."""

from . import sources, units, wiki, search  # noqa: F401

router = sources.router