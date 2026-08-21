#!/usr/bin/env python3
"""tests/conftest.py — Test path + env setup.

The control-api app treats its own directory as the package root (main.py does
`from config import config`, `from database import get_engine`, etc.). To import
those modules under test we put `apps/control_api` on sys.path and set the env
vars `config.py` requires.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTROL_API = ROOT / "apps" / "control_api"

for p in (str(ROOT), str(CONTROL_API)):
    if p not in sys.path:
        sys.path.insert(0, p)

# config.py (pydantic-settings) requires these at import time.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/knowledge_base",
)
os.environ.setdefault("API_TOKEN", "test-token")
