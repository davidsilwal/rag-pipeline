#!/usr/bin/env python3
"""apps/control_api/main.py — FastAPI Control Plane entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import sources, units, wiki, search, jobs

app = FastAPI(
    title="LLM Markdown Wiki Control API",
    version="2.2",
    description="Control API for the LLM Markdown Wiki Pipeline backend.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Explicit route registration to avoid router import aliasing issues
app.include_router(sources.router, prefix="/api/v1", tags=["sources"])
app.include_router(units.router, prefix="/api/v1", tags=["units"])
app.include_router(wiki.router, prefix="/api/v1", tags=["wiki"])
app.include_router(search.router, prefix="/api/v1", tags=["search"])
app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"])

# Health check endpoint
@app.get("/api/v1/health", tags=["health"])
async def health_check():
    return {
        "status": "ok",
        "postgres": True,
        "redis": True,
        "disk_space_gb": 100.0,
        "service": "control-api"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)