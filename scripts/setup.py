#!/usr/bin/env python
"""Setup script — installs all dependencies for the LLM Markdown Wiki Pipeline."""

import subprocess
import sys


def install_packages():
    """Install all required Python packages for the full pipeline."""
    pkgs = [
        # Core API
        "fastapi>=0.110.0",
        "uvicorn[standard]>=0.29.0",
        "pydantic>=2.6.0",
        "pydantic-settings>=2.2.0",
        # Database
        "asyncpg>=0.29.0",
        "sqlalchemy[asyncio]>=2.0.29",
        # ORM & Migrations
        "alembic>=1.13.0",
        # pgvector
        "pgvector>=1.5.1",
        # GPU Worker
        "litellm>=1.40.0",
        "FlagEmbedding>=2.0.0",
        "datasketch>=1.11.0",
        "umap-learn>=0.5.4",
        "hdbscan>=0.8.39",
        "numpy>=1.24.0",
        "pandas>=2.2.0",
        # Extraction
        "docling>=2.0.0",
        "MarkItDown[pdf,docx,eml,msg,html]≥0.0.1",
        "extract-msg>=0.25",
        "beautifulsoup4>=4.12.0",
        "openpyxl>=3.1.0",
        # Microsoft Graph
        "msgraph-sdk>=1.0.0",
        "msal>=1.28.0",
        "aiohttp>=3.9.0",
        # YAML
        "PyYAML>=6.0.0",
        # Testing
        "pytest>=8.0.0",
        "pytest-asyncio>=0.23.0",
        "pytest-cov>=4.1.0",
    ]
    # Fix syntax errors in package names
    pkgs = [p.split('≥')[0] if '≥' in p else p for p in pkgs]
    pkgs = [p.split('>=')[0] if '>=' not in p and '≥' in p else p for p in pkgs]
    # Deduplicate and clean
    seen = set()
    cleaned = []
    for p in pkgs:
        # Normalize: strip markers
        name = p.split('[')[0].split('>=')[0].split('≤')[0].split('≥')[0].split('==')[0].split('=')[0].strip()
        if name not in seen:
            seen.add(name)
            cleaned.append(p)
    
    print("Installing packages:")
    for p in cleaned:
        print(f"  - {p}")
    
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([sys.executable, "-m", "pip", "install", *cleaned])


if __name__ == "__main__":
    install_packages()
    print("\n✓ All dependencies installed successfully.")