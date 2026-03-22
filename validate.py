"""
Data quality validation endpoint.

POST /api/validate/data-quality
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import verify_api_key
from ..models.data_quality import DataQualityRequest, DataQualityResult
from ..services.data_quality import validate_data_quality

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/validate", tags=["Data Quality"])


@router.post(
    "/data-quality",
    response_model=DataQualityResult,
    summary="Validate patient record data quality",
    description=(
        "Scores a patient record across completeness, accuracy, "
        "timeliness, and clinical plausibility dimensions. Returns "
        "an overall score (0-100) with detailed issue breakdowns."
    ),
)
async def validate_data_quality_endpoint(
    request: DataQualityRequest,
    _api_key: str = Depends(verify_api_key),
) -> DataQualityResult:
    try:
        result = await validate_data_quality(request)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Data quality validation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error during data quality validation. Please try again.",
        )
