"""
Prompt engineering module for clinical data reconciliation.

DESIGN PHILOSOPHY
-----------------
These prompts follow a structured approach:

1. ROLE ANCHORING: The LLM is cast as a clinical pharmacist with reconciliation
   expertise, which activates domain-relevant reasoning patterns.

2. EVIDENCE HIERARCHY: Prompts encode a clear hierarchy for weighing sources:
   - Most recent clinical encounter > older records
   - Prescriber systems > pharmacy dispensing > patient self-report
   - High-reliability sources weighted more than low-reliability
   - Clinical context (labs, conditions) can override recency

3. STRUCTURED OUTPUT: Prompts request JSON responses with specific fields,
   reducing parsing ambiguity and ensuring consistent downstream processing.

4. SAFETY GUARDRAILS: Every reconciliation prompt includes a mandatory safety
   check step that flags contraindications, dangerous doses, or interactions
   before returning a recommendation.

5. SEPARATION OF CONCERNS: The rule-based evidence scorer runs BEFORE the LLM
   call and its output is included in the prompt. This gives the LLM a strong
   prior to refine rather than reasoning from scratch, improving both accuracy
   and consistency.
"""


def build_reconciliation_prompt(
    patient_context: dict,
    sources: list[dict],
    evidence_scores: dict[str, float],
) -> str:
    """
    Build the medication reconciliation prompt.

    Args:
        patient_context: Patient demographics, conditions, labs
        sources: List of conflicting medication records
        evidence_scores: Pre-computed evidence weights from the rule-based scorer
    """
    sources_formatted = "\n".join(
        f"  Source {i+1} [{s['system']}]: {s['medication']} "
        f"(reliability: {s.get('source_reliability', 'unknown')}, "
        f"last updated: {s.get('last_updated', s.get('last_filled', 'unknown'))})"
        for i, s in enumerate(sources)
    )

    scores_formatted = "\n".join(
        f"  {system}: weight={weight:.2f}"
        for system, weight in evidence_scores.items()
    )

    return f"""You are a clinical pharmacist performing medication reconciliation. Your task is to determine the most likely accurate medication regimen from conflicting records.

PATIENT CONTEXT:
  Age: {patient_context.get('age', 'unknown')}
  Active conditions: {', '.join(patient_context.get('conditions', [])) or 'None listed'}
  Recent labs: {_format_labs(patient_context.get('recent_labs'))}

CONFLICTING MEDICATION RECORDS:
{sources_formatted}

PRE-COMPUTED EVIDENCE WEIGHTS (from rule-based analysis):
{scores_formatted}

INSTRUCTIONS:
1. Analyze each source considering recency, reliability, and clinical context.
2. Consider whether the patient's conditions and lab values affect which dose is appropriate.
3. Determine the most likely current medication regimen.
4. Perform a safety check: flag any contraindications, dangerous doses, or missing information.
5. Suggest specific actions to resolve remaining discrepancies.

Respond with ONLY valid JSON in this exact structure (no markdown, no extra text):
{{
  "reconciled_medication": "<medication name, dose, and frequency>",
  "confidence_score": <float 0.0 to 1.0>,
  "reasoning": "<2-3 sentence clinical reasoning>",
  "recommended_actions": ["<action 1>", "<action 2>"],
  "clinical_safety_check": "<PASSED or WARNING or FAILED>",
  "safety_details": "<explanation if WARNING or FAILED, empty string if PASSED>"
}}"""


def build_data_quality_prompt(patient_record: dict, rule_based_issues: list[dict]) -> str:
    """
    Build the data quality assessment prompt.

    The rule-based validator runs first and catches obvious issues (implausible
    vitals, empty fields, stale data). The LLM then adds clinical reasoning
    that rules alone cannot capture (drug-disease mismatches, missing expected
    medications, incomplete allergy documentation).
    """
    pre_detected = "\n".join(
        f"  - [{issue['severity'].upper()}] {issue['field']}: {issue['issue']}"
        for issue in rule_based_issues
    ) or "  None detected by rule-based checks."

    return f"""You are a clinical data quality analyst reviewing a patient health record for completeness, accuracy, and clinical plausibility.

PATIENT RECORD:
{_format_record(patient_record)}

ISSUES ALREADY DETECTED BY RULE-BASED VALIDATION:
{pre_detected}

INSTRUCTIONS:
1. Review the record for clinical plausibility issues the rule-based system may have missed.
2. Check for drug-disease mismatches (e.g., medications that conflict with listed conditions).
3. Check for expected but missing data (e.g., diabetic patient with no glucose monitoring).
4. Assess whether the allergy list appears complete or suspiciously empty.
5. Do NOT repeat issues already detected above unless you have additional clinical context.

Respond with ONLY valid JSON (no markdown, no extra text):
{{
  "additional_issues": [
    {{
      "field": "<field name using dot notation>",
      "issue": "<description>",
      "severity": "<high or medium or low>"
    }}
  ],
  "clinical_observations": "<brief summary of overall clinical plausibility>"
}}"""


def _format_labs(labs) -> str:
    if not labs:
        return "None available"
    if isinstance(labs, dict):
        parts = [f"{k}: {v}" for k, v in labs.items() if v is not None]
        return ", ".join(parts) if parts else "None available"
    return str(labs)


def _format_record(record: dict) -> str:
    lines = []
    for key, value in record.items():
        if isinstance(value, dict):
            lines.append(f"  {key}:")
            for k, v in value.items():
                lines.append(f"    {k}: {v}")
        elif isinstance(value, list):
            items = ", ".join(str(i) for i in value) if value else "[]"
            lines.append(f"  {key}: {items}")
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)
