"""
Data quality validation service.

Scores patient records across four dimensions:
- COMPLETENESS: Are expected fields populated?
- ACCURACY: Do values pass format and range checks?
- TIMELINESS: How old is the data?
- CLINICAL PLAUSIBILITY: Are values physiologically possible?

Like the reconciliation service, this uses a hybrid approach.
Rule-based validators catch deterministic issues (impossible BP readings,
missing required fields, stale data) while the LLM catches clinical
subtleties (drug-disease mismatches, suspiciously empty allergy lists
in poly-pharmacy patients, expected labs missing for known conditions).
"""

import logging
import re
import uuid
from datetime import date, datetime
from typing import Optional

from ..models.data_quality import (
    DataQualityRequest,
    DataQualityResult,
    DetectedIssue,
    IssueSeverity,
    QualityBreakdown,
)
from .cache import quality_cache
from .llm_client import LLMClientError, llm_client
from .prompts import build_data_quality_prompt

logger = logging.getLogger(__name__)

# ── Physiological range definitions ──────────────────────────────────

VITAL_RANGES = {
    "heart_rate": (30, 220, "bpm"),
    "temperature": (90.0, 108.0, "F"),      # Fahrenheit
    "respiratory_rate": (6, 60, "breaths/min"),
    "oxygen_saturation": (50, 100, "%"),
    "weight_kg": (0.5, 350, "kg"),
    "height_cm": (30, 275, "cm"),
}

BP_SYSTOLIC_RANGE = (60, 250)
BP_DIASTOLIC_RANGE = (30, 150)

# ── Core scoring functions ───────────────────────────────────────────

def _score_completeness(record: DataQualityRequest) -> tuple[int, list[DetectedIssue]]:
    """Check which expected fields are populated."""
    issues = []
    populated = 0
    total = 0

    # Demographics (high weight)
    if record.demographics:
        demo_fields = ["name", "dob", "gender"]
        for field in demo_fields:
            total += 1
            if getattr(record.demographics, field, None):
                populated += 1
            else:
                issues.append(DetectedIssue(
                    field=f"demographics.{field}",
                    issue=f"Missing {field}",
                    severity=IssueSeverity.MEDIUM,
                ))
    else:
        total += 3
        issues.append(DetectedIssue(
            field="demographics",
            issue="No demographics provided",
            severity=IssueSeverity.HIGH,
        ))

    # Medications
    total += 1
    if record.medications and len(record.medications) > 0:
        populated += 1
    else:
        issues.append(DetectedIssue(
            field="medications",
            issue="No medications listed",
            severity=IssueSeverity.MEDIUM,
        ))

    # Allergies (empty is suspicious, not necessarily wrong)
    total += 1
    if record.allergies is not None:
        if len(record.allergies) == 0:
            populated += 0.5  # Partial credit: field exists but empty
            issues.append(DetectedIssue(
                field="allergies",
                issue="No allergies documented. Likely incomplete unless explicitly assessed as NKDA.",
                severity=IssueSeverity.MEDIUM,
            ))
        else:
            populated += 1

    # Conditions
    total += 1
    if record.conditions and len(record.conditions) > 0:
        populated += 1
    else:
        issues.append(DetectedIssue(
            field="conditions",
            issue="No conditions listed",
            severity=IssueSeverity.MEDIUM,
        ))

    # Vital signs
    total += 1
    if record.vital_signs:
        vital_count = sum(
            1 for f in ["blood_pressure", "heart_rate", "temperature", "respiratory_rate", "oxygen_saturation"]
            if getattr(record.vital_signs, f, None) is not None
        )
        if vital_count > 0:
            populated += min(vital_count / 3, 1.0)  # At least 3 vitals for full credit
        else:
            issues.append(DetectedIssue(
                field="vital_signs",
                issue="No vital signs recorded",
                severity=IssueSeverity.LOW,
            ))
    else:
        issues.append(DetectedIssue(
            field="vital_signs",
            issue="Vital signs section missing entirely",
            severity=IssueSeverity.LOW,
        ))

    score = int((populated / max(total, 1)) * 100)
    return score, issues


def _score_accuracy(record: DataQualityRequest) -> tuple[int, list[DetectedIssue]]:
    """Validate data format and range correctness."""
    issues = []
    checks_passed = 0
    checks_total = 0

    # DOB format validation
    if record.demographics and record.demographics.dob:
        checks_total += 1
        try:
            dob = date.fromisoformat(record.demographics.dob)
            if dob > date.today():
                issues.append(DetectedIssue(
                    field="demographics.dob",
                    issue="Date of birth is in the future",
                    severity=IssueSeverity.HIGH,
                ))
            elif (date.today() - dob).days > 365 * 150:
                issues.append(DetectedIssue(
                    field="demographics.dob",
                    issue="Date of birth implies age over 150 years",
                    severity=IssueSeverity.HIGH,
                ))
            else:
                checks_passed += 1
        except ValueError:
            issues.append(DetectedIssue(
                field="demographics.dob",
                issue="Invalid date format. Expected ISO 8601 (YYYY-MM-DD).",
                severity=IssueSeverity.HIGH,
            ))

    # Gender validation
    if record.demographics and record.demographics.gender:
        checks_total += 1
        valid_genders = {"M", "F", "Male", "Female", "Other", "Unknown", "Non-binary"}
        if record.demographics.gender in valid_genders:
            checks_passed += 1
        else:
            issues.append(DetectedIssue(
                field="demographics.gender",
                issue=f"Unrecognized gender value: '{record.demographics.gender}'",
                severity=IssueSeverity.LOW,
            ))

    # Medication format check (should contain dose info)
    if record.medications:
        for i, med in enumerate(record.medications):
            checks_total += 1
            if re.search(r"\d+\s*mg", med, re.IGNORECASE):
                checks_passed += 1
            else:
                issues.append(DetectedIssue(
                    field=f"medications[{i}]",
                    issue=f"Medication '{med}' missing dose information",
                    severity=IssueSeverity.LOW,
                ))

    score = int((checks_passed / max(checks_total, 1)) * 100)
    return score, issues


def _score_timeliness(record: DataQualityRequest) -> tuple[int, list[DetectedIssue]]:
    """Evaluate data freshness."""
    issues = []

    if not record.last_updated:
        return 50, [DetectedIssue(
            field="last_updated",
            issue="No last_updated timestamp. Cannot assess data freshness.",
            severity=IssueSeverity.MEDIUM,
        )]

    try:
        last_date = date.fromisoformat(record.last_updated)
    except ValueError:
        return 40, [DetectedIssue(
            field="last_updated",
            issue="Invalid date format for last_updated",
            severity=IssueSeverity.MEDIUM,
        )]

    days_old = (date.today() - last_date).days

    if days_old < 0:
        return 30, [DetectedIssue(
            field="last_updated",
            issue="last_updated is in the future",
            severity=IssueSeverity.HIGH,
        )]
    elif days_old <= 30:
        score = 100
    elif days_old <= 90:
        score = 85
    elif days_old <= 180:
        score = 70
        issues.append(DetectedIssue(
            field="last_updated",
            issue=f"Data is {days_old} days old (3-6 months)",
            severity=IssueSeverity.LOW,
        ))
    elif days_old <= 365:
        score = 50
        issues.append(DetectedIssue(
            field="last_updated",
            issue=f"Data is {days_old} days old (6-12 months). Consider refreshing.",
            severity=IssueSeverity.MEDIUM,
        ))
    else:
        score = 30
        issues.append(DetectedIssue(
            field="last_updated",
            issue=f"Data is {days_old} days old (over 1 year). Likely stale.",
            severity=IssueSeverity.HIGH,
        ))

    return score, issues


def _score_clinical_plausibility(record: DataQualityRequest) -> tuple[int, list[DetectedIssue]]:
    """Check vital signs and clinical values against physiological ranges."""
    issues = []
    checks_passed = 0
    checks_total = 0

    if record.vital_signs:
        # Blood pressure
        if record.vital_signs.blood_pressure:
            checks_total += 1
            bp_match = re.match(r"(\d+)/(\d+)", record.vital_signs.blood_pressure)
            if bp_match:
                systolic = int(bp_match.group(1))
                diastolic = int(bp_match.group(2))

                bp_issues = []
                if systolic < BP_SYSTOLIC_RANGE[0] or systolic > BP_SYSTOLIC_RANGE[1]:
                    bp_issues.append(f"systolic {systolic} outside {BP_SYSTOLIC_RANGE[0]}-{BP_SYSTOLIC_RANGE[1]}")
                if diastolic < BP_DIASTOLIC_RANGE[0] or diastolic > BP_DIASTOLIC_RANGE[1]:
                    bp_issues.append(f"diastolic {diastolic} outside {BP_DIASTOLIC_RANGE[0]}-{BP_DIASTOLIC_RANGE[1]}")
                if diastolic >= systolic:
                    bp_issues.append("diastolic >= systolic")

                if bp_issues:
                    issues.append(DetectedIssue(
                        field="vital_signs.blood_pressure",
                        issue=f"Blood pressure {record.vital_signs.blood_pressure} is physiologically implausible ({'; '.join(bp_issues)})",
                        severity=IssueSeverity.HIGH,
                    ))
                else:
                    checks_passed += 1
            else:
                issues.append(DetectedIssue(
                    field="vital_signs.blood_pressure",
                    issue=f"Blood pressure format invalid: '{record.vital_signs.blood_pressure}'. Expected SYS/DIA.",
                    severity=IssueSeverity.MEDIUM,
                ))

        # Other vital signs
        for field_name, (low, high, unit) in VITAL_RANGES.items():
            value = getattr(record.vital_signs, field_name, None)
            if value is not None:
                checks_total += 1
                if low <= value <= high:
                    checks_passed += 1
                else:
                    issues.append(DetectedIssue(
                        field=f"vital_signs.{field_name}",
                        issue=f"{field_name} value {value} {unit} outside plausible range ({low}-{high})",
                        severity=IssueSeverity.HIGH,
                    ))

    if checks_total == 0:
        return 70, issues  # No vitals to check; neutral score

    score = int((checks_passed / checks_total) * 100)
    return score, issues


# ── Main validation entry point ──────────────────────────────────────

async def validate_data_quality(request: DataQualityRequest) -> DataQualityResult:
    """
    Run the full data quality assessment.

    1. Check cache
    2. Run all rule-based validators
    3. Call LLM for additional clinical observations
    4. Combine scores and return
    """
    cache_key_data = request.model_dump(mode="json")
    cached = quality_cache.get(cache_key_data)
    if cached:
        logger.info("Cache hit for data quality request")
        return DataQualityResult(**cached)

    # Run rule-based validators
    completeness_score, completeness_issues = _score_completeness(request)
    accuracy_score, accuracy_issues = _score_accuracy(request)
    timeliness_score, timeliness_issues = _score_timeliness(request)
    plausibility_score, plausibility_issues = _score_clinical_plausibility(request)

    all_issues = completeness_issues + accuracy_issues + timeliness_issues + plausibility_issues

    # LLM augmentation for clinical subtleties
    try:
        prompt = build_data_quality_prompt(
            patient_record=request.model_dump(mode="json"),
            rule_based_issues=[i.model_dump() for i in all_issues],
        )
        llm_result = llm_client.complete(prompt)

        additional = llm_result.get("additional_issues", [])
        for item in additional:
            if isinstance(item, dict) and "field" in item and "issue" in item:
                all_issues.append(DetectedIssue(
                    field=item["field"],
                    issue=item["issue"],
                    severity=_parse_severity(item.get("severity", "low")),
                ))

    except LLMClientError as e:
        logger.warning(f"LLM unavailable for quality check: {e}")

    # Compute overall score (weighted average)
    overall = int(
        completeness_score * 0.30
        + accuracy_score * 0.25
        + timeliness_score * 0.20
        + plausibility_score * 0.25
    )

    result = DataQualityResult(
        overall_score=overall,
        breakdown=QualityBreakdown(
            completeness=completeness_score,
            accuracy=accuracy_score,
            timeliness=timeliness_score,
            clinical_plausibility=plausibility_score,
        ),
        issues_detected=all_issues,
        validation_id=str(uuid.uuid4()),
    )

    quality_cache.set(cache_key_data, result.model_dump(mode="json"))
    return result


def _parse_severity(raw: str) -> IssueSeverity:
    try:
        return IssueSeverity(raw.lower().strip())
    except ValueError:
        return IssueSeverity.LOW
