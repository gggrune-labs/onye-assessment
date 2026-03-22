"""
Pydantic models for the medication reconciliation endpoint.

Models use FHIR-aware naming conventions (MedicationStatement, Patient context)
while keeping the JSON structure accessible for the assessment's example schema.
"""

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SourceReliability(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClinicalSafetyStatus(str, Enum):
    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"


class RecentLabs(BaseModel):
    """Laboratory results relevant to medication decisions."""
    eGFR: Optional[float] = Field(None, description="Estimated glomerular filtration rate")
    hba1c: Optional[float] = Field(None, description="Hemoglobin A1c percentage")
    creatinine: Optional[float] = Field(None, description="Serum creatinine mg/dL")
    potassium: Optional[float] = Field(None, description="Serum potassium mEq/L")
    inr: Optional[float] = Field(None, description="International normalized ratio")


class PatientContext(BaseModel):
    """
    Patient demographic and clinical context.
    Maps loosely to FHIR Patient + Condition resources.
    """
    age: int = Field(..., ge=0, le=150, description="Patient age in years")
    conditions: list[str] = Field(default_factory=list, description="Active diagnoses")
    recent_labs: Optional[RecentLabs] = None

    @field_validator("age")
    @classmethod
    def validate_age(cls, v: int) -> int:
        if v < 0 or v > 150:
            raise ValueError("Age must be between 0 and 150")
        return v


class MedicationSource(BaseModel):
    """
    A single medication record from one clinical source.
    Maps loosely to FHIR MedicationStatement with provenance.
    """
    system: str = Field(..., min_length=1, description="Source system name")
    medication: str = Field(..., min_length=1, description="Medication name, dose, frequency")
    last_updated: Optional[date] = Field(None, description="When this record was last modified")
    last_filled: Optional[date] = Field(None, description="Pharmacy fill date")
    source_reliability: SourceReliability = Field(
        SourceReliability.MEDIUM,
        description="Assessed reliability of this source"
    )

    @field_validator("last_updated", "last_filled", mode="before")
    @classmethod
    def parse_date_string(cls, v):
        if v is None:
            return v
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v


class ReconciliationRequest(BaseModel):
    """Top-level request body for POST /api/reconcile/medication."""
    patient_context: PatientContext
    sources: list[MedicationSource] = Field(
        ..., min_length=2, description="At least two conflicting sources required"
    )


class ReconciliationResult(BaseModel):
    """Response body for the medication reconciliation endpoint."""
    reconciled_medication: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    recommended_actions: list[str]
    clinical_safety_check: ClinicalSafetyStatus
    source_weights: Optional[dict[str, float]] = Field(
        None, description="Computed evidence weight per source"
    )
    reconciliation_id: str = Field(..., description="Unique ID for audit trail")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
