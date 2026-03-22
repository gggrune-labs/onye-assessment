"""
Medication reconciliation service.

HYBRID ARCHITECTURE
-------------------
This is the core differentiator of the engine. Rather than sending raw
conflicting records straight to the LLM and hoping for the best, we
implement a two-phase approach:

Phase 1 (Rule-Based Evidence Scoring):
  Computes a numerical weight for each source based on:
  - RECENCY: More recent records score higher (exponential decay)
  - RELIABILITY: Source reliability tier multiplied in
  - CLINICAL CONTEXT: Lab values and conditions that affect dosing decisions
  - CONCORDANCE: Sources that agree with each other get a bonus

Phase 2 (LLM Clinical Reasoning):
  The pre-computed weights are fed INTO the prompt so the LLM has a strong
  quantitative prior. The LLM then applies clinical judgment that rules alone
  cannot capture (pharmacokinetic reasoning, guideline awareness, etc.)
  and returns the final reconciliation.

This approach is more reliable than pure LLM inference because:
  1. The rule-based scores are deterministic and auditable
  2. The LLM refines rather than reasons from scratch
  3. If the LLM is unavailable, we can fall back to rule-based results
"""

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from ..models.medication import (
    ClinicalSafetyStatus,
    MedicationSource,
    PatientContext,
    ReconciliationRequest,
    ReconciliationResult,
    SourceReliability,
)
from .cache import reconciliation_cache
from .llm_client import LLMClientError, llm_client
from .prompts import build_reconciliation_prompt

logger = logging.getLogger(__name__)

# Evidence weight constants
RELIABILITY_WEIGHTS = {
    SourceReliability.HIGH: 1.0,
    SourceReliability.MEDIUM: 0.7,
    SourceReliability.LOW: 0.4,
}

RECENCY_HALF_LIFE_DAYS = 90  # Weight halves every 90 days


def compute_evidence_weights(
    sources: list[MedicationSource],
    patient_context: PatientContext,
) -> dict[str, float]:
    """
    Phase 1: Rule-based evidence scoring.

    Returns a normalized weight for each source system.
    """
    today = date.today()
    raw_weights: dict[str, float] = {}

    for source in sources:
        weight = 1.0

        # Factor 1: Source reliability tier
        weight *= RELIABILITY_WEIGHTS.get(source.source_reliability, 0.5)

        # Factor 2: Recency (exponential decay)
        ref_date = source.last_updated or source.last_filled
        if ref_date:
            days_old = (today - ref_date).days
            recency_factor = 0.5 ** (days_old / RECENCY_HALF_LIFE_DAYS)
            weight *= max(recency_factor, 0.1)  # Floor at 0.1, never fully discard
        else:
            weight *= 0.3  # No date = significant penalty

        # Factor 3: Clinical context adjustments
        weight *= _apply_clinical_context(source, patient_context)

        raw_weights[source.system] = weight

    # Factor 4: Concordance bonus
    raw_weights = _apply_concordance_bonus(sources, raw_weights)

    # Normalize to sum to 1.0
    total = sum(raw_weights.values())
    if total > 0:
        return {k: round(v / total, 4) for k, v in raw_weights.items()}
    return {k: round(1.0 / len(raw_weights), 4) for k in raw_weights}


def _apply_clinical_context(
    source: MedicationSource,
    patient: PatientContext,
) -> float:
    """
    Adjust evidence weight based on clinical context.

    Example: If a patient has declining kidney function (low eGFR),
    a lower dose is more clinically plausible than a higher dose.
    """
    multiplier = 1.0
    med_lower = source.medication.lower()

    if patient.recent_labs:
        # Kidney function affects many drug doses
        if patient.recent_labs.eGFR and patient.recent_labs.eGFR < 60:
            # For renally cleared drugs, lower doses are more plausible
            renal_drugs = ["metformin", "gabapentin", "lisinopril", "allopurinol"]
            if any(drug in med_lower for drug in renal_drugs):
                dose = _extract_dose_mg(source.medication)
                if dose is not None and dose <= 500:
                    multiplier *= 1.2  # Boost plausibility of lower dose
                elif dose is not None and dose >= 1000:
                    multiplier *= 0.8  # Penalize higher dose

    return multiplier


def _apply_concordance_bonus(
    sources: list[MedicationSource],
    weights: dict[str, float],
) -> dict[str, float]:
    """
    Sources that agree on the same medication/dose get a concordance bonus.
    Agreement is determined by normalized string matching.
    """
    normalized = {}
    for s in sources:
        norm = _normalize_medication_string(s.medication)
        if norm not in normalized:
            normalized[norm] = []
        normalized[norm].append(s.system)

    # Find the largest agreement cluster
    for med_str, systems in normalized.items():
        if len(systems) > 1:
            bonus = 1.0 + (0.15 * (len(systems) - 1))
            for sys_name in systems:
                if sys_name in weights:
                    weights[sys_name] *= bonus

    return weights


def _normalize_medication_string(med: str) -> str:
    """Normalize medication string for comparison."""
    return med.lower().strip().replace("  ", " ")


def _extract_dose_mg(medication: str) -> Optional[float]:
    """Extract numeric dose in mg from a medication string."""
    import re
    match = re.search(r"(\d+(?:\.\d+)?)\s*mg", medication.lower())
    if match:
        return float(match.group(1))
    return None


async def reconcile_medication(request: ReconciliationRequest) -> ReconciliationResult:
    """
    Main reconciliation entry point.

    1. Check cache for identical previous request
    2. Run rule-based evidence scoring (Phase 1)
    3. Call LLM with evidence scores for clinical reasoning (Phase 2)
    4. Build and cache the response
    """
    # Check cache
    cache_key_data = request.model_dump(mode="json")
    cached = reconciliation_cache.get(cache_key_data)
    if cached:
        logger.info("Cache hit for reconciliation request")
        return ReconciliationResult(**cached)

    # Phase 1: Rule-based evidence scoring
    evidence_weights = compute_evidence_weights(
        request.sources, request.patient_context
    )

    # Phase 2: LLM clinical reasoning
    try:
        prompt = build_reconciliation_prompt(
            patient_context=request.patient_context.model_dump(mode="json"),
            sources=[s.model_dump(mode="json") for s in request.sources],
            evidence_scores=evidence_weights,
        )
        llm_result = llm_client.complete(prompt)

        # Merge rule-based weights with LLM result
        result = ReconciliationResult(
            reconciled_medication=llm_result.get("reconciled_medication", "Unable to determine"),
            confidence_score=_clamp(llm_result.get("confidence_score", 0.5), 0.0, 1.0),
            reasoning=llm_result.get("reasoning", ""),
            recommended_actions=llm_result.get("recommended_actions", []),
            clinical_safety_check=_parse_safety_status(
                llm_result.get("clinical_safety_check", "WARNING")
            ),
            source_weights=evidence_weights,
            reconciliation_id=str(uuid.uuid4()),
        )

    except LLMClientError as e:
        logger.error(f"LLM unavailable, falling back to rule-based: {e}")
        result = _fallback_reconciliation(request, evidence_weights)

    # Cache the result
    reconciliation_cache.set(cache_key_data, result.model_dump(mode="json"))
    return result


def _fallback_reconciliation(
    request: ReconciliationRequest,
    weights: dict[str, float],
) -> ReconciliationResult:
    """
    Rule-based fallback when the LLM is unavailable.
    Selects the medication from the highest-weighted source.
    """
    best_source = max(request.sources, key=lambda s: weights.get(s.system, 0))
    return ReconciliationResult(
        reconciled_medication=best_source.medication,
        confidence_score=round(max(weights.values()) * 0.7, 2),  # Discount without LLM
        reasoning=(
            f"LLM unavailable. Rule-based selection chose {best_source.system} "
            f"(weight: {weights.get(best_source.system, 0):.2f}) based on recency, "
            f"reliability, and clinical context scoring."
        ),
        recommended_actions=[
            "Manual clinical review recommended (AI reasoning unavailable)",
            f"Verify current regimen with {best_source.system}",
        ],
        clinical_safety_check=ClinicalSafetyStatus.WARNING,
        source_weights=weights,
        reconciliation_id=str(uuid.uuid4()),
    )


def _clamp(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))


def _parse_safety_status(raw: str) -> ClinicalSafetyStatus:
    normalized = raw.upper().strip()
    try:
        return ClinicalSafetyStatus(normalized)
    except ValueError:
        return ClinicalSafetyStatus.WARNING
