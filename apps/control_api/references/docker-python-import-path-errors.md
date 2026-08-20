# Docker Python Import Path Errors - Session Reference

## Context
2026-08-20 session: Building LLM Markdown Wiki Pipeline control-api Docker container.

## Exact Errors Encountered
```
1. ImportError: attempted relative import with no known parent package
2. ModuleNotFoundError: No module named 'pgvector'  
3. ImportError: cannot import name 'Vector' from 'sqlalchemy.dialects.postgresql'
4. TypeError: Boolean value of this clause is not defined (SQLAlchemy column primary_key)
```

## Root Causes & Fixes

### Fix 1: Absolute vs Relative Imports
**Problem**: Code at container path `/app/` with `PYTHONPATH=/app`. Files like `main.py`, `routers/sources.py`, `models/__init__.py` use `from . import create_app` (relative) which fails when `main:app` is imported directly by uvicorn.

**Fix**: Use absolute imports for all sibling modules:
```python
# ✅ Correct (inside Docker container with PYTHONPATH=/app):
from routers import sources, units, wiki, search, jobs
from database import get_engine
from models import Source
from config import config

# ❌ Incorrect:
from . import create_app
from ..database import get_engine
from .routers import sources
```

### Fix 2: pgvector Python Package Missing
**Problem**: `pgvector/pgvector:pg16` Docker image includes PostgreSQL pgvector extension but NOT the Python `pgvector` package.

**Fix**: Add to requirements.txt:
```
pgvector>=0.4.0
```
And import via:
```python
from pgvector.sqlalchemy import Vector  # ✅
# NOT from sqlalchemy.dialects.postgresql import Vector  # ❌
```

### Fix 3: SQLAlchemy UUID Primary Key
**Problem**: `Column(UUID, primary_key=func.uuid_generate_v4())` - passing func expression to primary_key parameter breaks SQLAlchemy's boolean check.

**Fix**: Use `server_default` with text():
```python
cluster_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
```

### Fix 4: Docker Build Caching
**Problem**: Changes to Python source files don't get picked up because Docker uses cached layers.

**Fix**: Always rebuild with --no-cache:
```bash
docker compose build --no-cache control-api
docker compose up -d --force-recreate control-api
```

## Key Dockerfile Configuration
```dockerfile
FROM python:3.12-alpine
ENV PYTHONPATH=/app          # Essential for absolute imports
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir pgvector  # Include pgvector
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```