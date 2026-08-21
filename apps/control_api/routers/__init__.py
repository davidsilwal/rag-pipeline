#!/usr/bin/env python3
"""apps/control_api/routers/__init__.py — Router package marker."""

from . import sources, units, wiki, search, jobs, workers, tasks, embed_cache, system  # noqa: F401

router = sources.router
