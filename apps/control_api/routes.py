# routes.py — Central API route registration
from fastapi import APIRouter

from routers import sources, units, wiki, search, jobs, workers, tasks, embed_cache, system

router = APIRouter()
router.include_router(sources.router, prefix="/api/v1", tags=["sources"])
router.include_router(units.router, prefix="/api/v1", tags=["units"])
router.include_router(wiki.router, prefix="/api/v1", tags=["wiki"])
router.include_router(search.router, prefix="/api/v1", tags=["search"])
router.include_router(jobs.router, prefix="/api/v1", tags=["jobs"])
router.include_router(workers.router, prefix="/api/v1", tags=["workers"])
router.include_router(tasks.router, prefix="/api/v1", tags=["tasks"])
router.include_router(embed_cache.router, prefix="/api/v1", tags=["embed_cache"])
router.include_router(system.router, prefix="/api/v1", tags=["system"])

api_router = router

__all__ = ["api_router"]
