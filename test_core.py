"""
Unit tests for the Clinical Data Reconciliation Engine.

Tests cover:
1. Evidence weight computation (recency, reliability, clinical context)
2. Concordance bonus calculation
3. Data quality scoring across all four dimensions
4. Cache behavior (hit, miss, expiry)
5. Input validation via Pydantic models
6. Fallback reconciliation when LLM is unavailable
7. Vital sign plausibility detection
"""

import json
import time
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.medication import (
    ClinicalSafetyStatus,
    MedicationSource,
    PatientContext,
    RecentLabs,
    ReconciliationRequest,
    SourceReliability,
)
from app.models.data_quality import (
    DataQualityRequest,
    Demographics,
    IssueSeverity,
    VitalSigns,
)
from app.services.reconciliation import (
    _apply_concordance_bonus,
    _extract_dose_mg,
    _fallback_reconciliation,
    compute_evidence_weights,
)
from app.services.data_quality import (
    _score_clinical_plausibility,
    _score_completeness,
    _score_accuracy,
    _score_timeliness,
)
from app.services.cache import ResponseCache


# ── Test 1: Evidence weight computation ──────────────────────────────

class TestEvidenceWeights:
    def test_recent_high_reliability_source_wins(self):
        """A recent, high-reliability source should have the highest weight."""
        today = date.today()
        sources = [
            MedicationSource(
                system="Recent Clinic",
                medication="Metformin 500mg twice daily",
                last_updated=today - timedelta(days=5),
                source_reliability=SourceReliability.HIGH,
            ),
            MedicationSource(
                system="Old Hospital",
                medication="Metformin 1000mg twice daily",
                last_updated=today - timedelta(days=200),
                source_reliability=SourceReliability.HIGH,
            ),
        ]
        patient = PatientContext(age=65, conditions=["Type 2 Diabetes"])

        weights = compute_evidence_weights(sources, patient)

        assert weights["Recent Clinic"] > weights["Old Hospital"]

    def test_low_reliability_penalized(self):
        """A low-reliability source should be penalized even if recent."""
        today = date.today()
        sources = [
            MedicationSource(
                system="Patient Portal",
                medication="Aspirin 81mg daily",
                last_updated=today - timedelta(days=2),
                source_reliability=SourceReliability.LOW,
            ),
            MedicationSource(
                system="Hospital EHR",
                medication="Aspirin 81mg daily",
                last_updated=today - timedelta(days=30),
                source_reliability=SourceReliability.HIGH,
            ),
        ]
        patient = PatientContext(age=70, conditions=["Hypertension"])

        weights = compute_evidence_weights(sources, patient)

        assert weights["Hospital EHR"] > weights["Patient Portal"]

    def test_renal_context_boosts_lower_dose(self):
        """For a patient with low eGFR, a lower metformin dose should be boosted."""
        today = date.today()
        sources = [
            MedicationSource(
                system="High Dose Source",
                medication="Metformin 1000mg twice daily",
                last_updated=today - timedelta(days=10),
                source_reliability=SourceReliability.HIGH,
            ),
            MedicationSource(
                system="Low Dose Source",
                medication="Metformin 500mg twice daily",
                last_updated=today - timedelta(days=10),
                source_reliability=SourceReliability.HIGH,
            ),
        ]
        patient = PatientContext(
            age=67,
            conditions=["Type 2 Diabetes", "CKD Stage 3"],
            recent_labs=RecentLabs(eGFR=42),
        )

        weights = compute_evidence_weights(sources, patient)

        assert weights["Low Dose Source"] > weights["High Dose Source"]

    def test_missing_date_penalized(self):
        """Sources without any date should receive a penalty."""
        today = date.today()
        sources = [
            MedicationSource(
                system="Dated Source",
                medication="Lisinopril 10mg daily",
                last_updated=today - timedelta(days=30),
                source_reliability=SourceReliability.MEDIUM,
            ),
            MedicationSource(
                system="Undated Source",
                medication="Lisinopril 10mg daily",
                source_reliability=SourceReliability.MEDIUM,
            ),
        ]
        patient = PatientContext(age=60, conditions=[])

        weights = compute_evidence_weights(sources, patient)

        assert weights["Dated Source"] > weights["Undated Source"]

    def test_weights_sum_to_one(self):
        """All weights should be normalized to sum to 1.0."""
        today = date.today()
        sources = [
            MedicationSource(
                system=f"Source {i}",
                medication="Test 100mg daily",
                last_updated=today - timedelta(days=i * 30),
                source_reliability=SourceReliability.HIGH,
            )
            for i in range(4)
        ]
        patient = PatientContext(age=50, conditions=[])

        weights = compute_evidence_weights(sources, patient)

        assert abs(sum(weights.values()) - 1.0) < 0.01


# ── Test 2: Concordance bonus ────────────────────────────────────────

class TestConcordance:
    def test_agreeing_sources_boosted(self):
        """Sources with matching medications should receive a concordance bonus."""
        sources = [
            MedicationSource(
                system="A", medication="Atorvastatin 40mg daily",
                source_reliability=SourceReliability.HIGH,
            ),
            MedicationSource(
                system="B", medication="Atorvastatin 40mg daily",
                source_reliability=SourceReliability.MEDIUM,
            ),
            MedicationSource(
                system="C", medication="Atorvastatin 20mg daily",
                source_reliability=SourceReliability.HIGH,
            ),
        ]
        weights = {"A": 1.0, "B": 0.7, "C": 1.0}

        updated = _apply_concordance_bonus(sources, weights)

        assert updated["A"] > 1.0
        assert updated["B"] > 0.7
        assert updated["C"] == 1.0  # No concordance partner


# ── Test 3: Data quality scoring ─────────────────────────────────────

class TestDataQualityScoring:
    def test_implausible_bp_detected(self):
        """Blood pressure 340/180 should be flagged as implausible."""
        record = DataQualityRequest(
            vital_signs=VitalSigns(blood_pressure="340/180"),
        )
        score, issues = _score_clinical_plausibility(record)

        assert score < 50
        high_severity = [i for i in issues if i.severity == IssueSeverity.HIGH]
        assert len(high_severity) >= 1
        assert any("blood_pressure" in i.field for i in high_severity)

    def test_normal_vitals_pass(self):
        """Normal vital signs should score 100 plausibility."""
        record = DataQualityRequest(
            vital_signs=VitalSigns(
                blood_pressure="120/80",
                heart_rate=72,
                temperature=98.6,
                oxygen_saturation=98,
            ),
        )
        score, issues = _score_clinical_plausibility(record)

        assert score == 100
        assert len(issues) == 0

    def test_completeness_flags_empty_allergies(self):
        """An empty allergies list should be flagged as potentially incomplete."""
        record = DataQualityRequest(
            demographics=Demographics(name="Test", dob="1990-01-01", gender="M"),
            medications=["Metformin 500mg"],
            allergies=[],
            conditions=["Diabetes"],
            vital_signs=VitalSigns(blood_pressure="120/80"),
        )
        score, issues = _score_completeness(record)

        allergy_issues = [i for i in issues if "allerg" in i.field.lower()]
        assert len(allergy_issues) >= 1

    def test_timeliness_stale_data(self):
        """Data older than 1 year should get a low timeliness score."""
        record = DataQualityRequest(
            last_updated=(date.today() - timedelta(days=400)).isoformat(),
        )
        score, issues = _score_timeliness(record)

        assert score <= 30
        assert len(issues) >= 1

    def test_timeliness_fresh_data(self):
        """Data updated within 30 days should score 100."""
        record = DataQualityRequest(
            last_updated=(date.today() - timedelta(days=5)).isoformat(),
        )
        score, issues = _score_timeliness(record)

        assert score == 100

    def test_accuracy_future_dob(self):
        """A date of birth in the future should be flagged."""
        record = DataQualityRequest(
            demographics=Demographics(
                name="Test",
                dob=(date.today() + timedelta(days=30)).isoformat(),
                gender="M",
            ),
        )
        score, issues = _score_accuracy(record)

        assert any("future" in i.issue.lower() for i in issues)


# ── Test 4: Cache behavior ───────────────────────────────────────────

class TestCache:
    def test_cache_hit(self):
        """Stored responses should be retrievable."""
        cache = ResponseCache(ttl_seconds=60)
        data = {"patient_context": {"age": 65}, "sources": []}
        response = {"reconciled_medication": "Test", "confidence_score": 0.9}

        cache.set(data, response)
        result = cache.get(data)

        assert result is not None
        assert result["reconciled_medication"] == "Test"

    def test_cache_miss(self):
        """Unknown keys should return None."""
        cache = ResponseCache(ttl_seconds=60)
        result = cache.get({"unknown": "request"})

        assert result is None

    def test_cache_expiry(self):
        """Expired entries should not be returned."""
        cache = ResponseCache(ttl_seconds=1)
        data = {"test": "data"}
        cache.set(data, {"result": "cached"})

        time.sleep(1.5)
        result = cache.get(data)

        assert result is None

    def test_cache_eviction(self):
        """Cache should evict oldest entry when full."""
        cache = ResponseCache(ttl_seconds=60, max_entries=2)
        cache.set({"req": 1}, {"res": 1})
        time.sleep(0.01)
        cache.set({"req": 2}, {"res": 2})
        cache.set({"req": 3}, {"res": 3})  # Should evict req 1

        assert cache.get({"req": 1}) is None
        assert cache.get({"req": 2}) is not None
        assert cache.size == 2


# ── Test 5: Input validation ─────────────────────────────────────────

class TestInputValidation:
    def test_age_out_of_range_rejected(self):
        """Age above 150 should fail validation."""
        with pytest.raises(Exception):
            PatientContext(age=200, conditions=[])

    def test_negative_age_rejected(self):
        """Negative age should fail validation."""
        with pytest.raises(Exception):
            PatientContext(age=-5, conditions=[])

    def test_minimum_two_sources_required(self):
        """ReconciliationRequest requires at least 2 sources."""
        with pytest.raises(Exception):
            ReconciliationRequest(
                patient_context=PatientContext(age=50, conditions=[]),
                sources=[
                    MedicationSource(
                        system="Only One",
                        medication="Test 10mg",
                        source_reliability=SourceReliability.HIGH,
                    )
                ],
            )

    def test_valid_request_accepted(self):
        """A properly formed request should pass validation."""
        req = ReconciliationRequest(
            patient_context=PatientContext(age=50, conditions=["Hypertension"]),
            sources=[
                MedicationSource(
                    system="Source A",
                    medication="Lisinopril 10mg daily",
                    last_updated="2025-01-01",
                    source_reliability=SourceReliability.HIGH,
                ),
                MedicationSource(
                    system="Source B",
                    medication="Lisinopril 20mg daily",
                    last_updated="2025-02-01",
                    source_reliability=SourceReliability.HIGH,
                ),
            ],
        )
        assert req.patient_context.age == 50
        assert len(req.sources) == 2


# ── Test 6: Fallback reconciliation ──────────────────────────────────

class TestFallbackReconciliation:
    def test_fallback_selects_highest_weighted_source(self):
        """When LLM is down, fallback should pick the highest-weight source."""
        request = ReconciliationRequest(
            patient_context=PatientContext(age=60, conditions=[]),
            sources=[
                MedicationSource(
                    system="Weak Source",
                    medication="Drug A 10mg",
                    source_reliability=SourceReliability.LOW,
                ),
                MedicationSource(
                    system="Strong Source",
                    medication="Drug B 20mg",
                    source_reliability=SourceReliability.HIGH,
                ),
            ],
        )
        weights = {"Weak Source": 0.2, "Strong Source": 0.8}

        result = _fallback_reconciliation(request, weights)

        assert result.reconciled_medication == "Drug B 20mg"
        assert result.clinical_safety_check == ClinicalSafetyStatus.WARNING
        assert result.confidence_score < 0.8  # Discounted without LLM


# ── Test 7: Dose extraction utility ──────────────────────────────────

class TestDoseExtraction:
    def test_extract_simple_dose(self):
        assert _extract_dose_mg("Metformin 500mg twice daily") == 500.0

    def test_extract_decimal_dose(self):
        assert _extract_dose_mg("Warfarin 2.5mg daily") == 2.5

    def test_no_dose_returns_none(self):
        assert _extract_dose_mg("Not currently taking aspirin") is None

    def test_extract_from_units(self):
        assert _extract_dose_mg("Lisinopril 10 mg daily") == 10.0
