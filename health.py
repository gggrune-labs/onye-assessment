"""
Health check and system status endpoint.
"""

from fastapi import APIRouter

from ..services.cache import quality_cache, reconciliation_cache
from ..services.llm_client import llm_client

router = APIRouter(tags=["System"])


@router.get("/health", summary="System health check")
async def health_check():
    return {
        "status": "healthy",
        "llm_requests_served": llm_client.request_count,
        "reconciliation_cache_size": reconciliation_cache.size,
        "quality_cache_size": quality_cache.size,
    }
