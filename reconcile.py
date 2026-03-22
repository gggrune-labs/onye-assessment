"""
Medication reconciliation endpoint.

POST /api/reconcile/medication
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import verify_api_key
from ..models.medication import ReconciliationRequest, ReconciliationResult
from ..services.reconciliation import reconcile_medication

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reconcile", tags=["Reconciliation"])


@router.post(
    "/medication",
    response_model=ReconciliationResult,
    summary="Reconcile conflicting medication records",
    description=(
        "Accepts an array of conflicting medication records from different "
        "clinical sources and returns the most likely accurate medication "
        "regimen with confidence scoring and clinical reasoning."
    ),
)
async def reconcile_medication_endpoint(
    request: ReconciliationRequest,
    _api_key: str = Depends(verify_api_key),
) -> ReconciliationResult:
    try:
        result = await reconcile_medication(request)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Reconciliation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error during medication reconciliation. Please try again.",
        )
