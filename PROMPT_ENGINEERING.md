# Prompt Engineering Approach

## Overview

The reconciliation engine uses two distinct prompts: one for medication reconciliation and one for data quality assessment. Both follow the same structural principles but serve different clinical purposes.

## Design Principles

### 1. Role Anchoring

Both prompts begin by casting Claude as a domain-specific clinician:

- **Reconciliation**: "You are a clinical pharmacist performing medication reconciliation."
- **Data quality**: "You are a clinical data quality analyst reviewing a patient health record."

Role anchoring activates domain-relevant reasoning patterns. A generic "you are a helpful assistant" prompt would produce less clinically specific reasoning. The pharmacist framing is deliberate because medication reconciliation is literally a pharmacist's job in hospital settings.

### 2. Structured Context Injection

Rather than dumping raw JSON into the prompt, patient data is formatted into labeled sections:

```
PATIENT CONTEXT:
  Age: 67
  Active conditions: Type 2 Diabetes, Hypertension
  Recent labs: eGFR: 45, hba1c: 7.2
```

This mirrors how clinical information is presented in medical documentation (structured, labeled, scannable). It helps the model parse the relevant factors without needing to extract them from nested JSON.

### 3. Evidence Priors from Phase 1

The reconciliation prompt includes a section called "PRE-COMPUTED EVIDENCE WEIGHTS" that contains the output of the rule-based scorer:

```
PRE-COMPUTED EVIDENCE WEIGHTS (from rule-based analysis):
  Hospital EHR: weight=0.22
  Primary Care: weight=0.45
  Pharmacy: weight=0.33
```

This is the key architectural decision. Instead of asking the LLM to evaluate recency, reliability, and clinical context from scratch, the prompt gives it a quantitative starting point. The LLM then applies clinical judgment to refine these weights (e.g., recognizing that an anticoagulation clinic's warfarin dose recommendation should carry extra weight for an A-fib patient, even if the rule-based score does not account for that specific specialty-condition relationship).

### 4. Explicit Instruction Sequence

The prompt includes numbered steps that mirror clinical reasoning:

1. Analyze each source considering recency, reliability, and clinical context
2. Consider whether patient conditions and lab values affect appropriate dosing
3. Determine the most likely current medication regimen
4. Perform a safety check (contraindications, dangerous doses)
5. Suggest actions to resolve remaining discrepancies

This step-by-step structure improves reasoning quality by preventing the model from jumping to a conclusion before considering all factors. It also makes the model's reasoning process more transparent.

### 5. Strict Output Format

Both prompts end with:

```
Respond with ONLY valid JSON in this exact structure (no markdown, no extra text):
```

Followed by the complete JSON template with type annotations. This is essential for programmatic parsing. The "no markdown, no extra text" instruction prevents the model from wrapping the JSON in code fences or adding conversational preamble.

### 6. Safety Guardrails

Every reconciliation prompt includes a mandatory safety check step. The model must classify the result as PASSED, WARNING, or FAILED and provide details for non-PASSED results. This ensures that even when the model is confident about a reconciliation, it still explicitly considers:

- Drug-drug interactions
- Drug-disease contraindications
- Dose plausibility for the patient's age and organ function
- Missing information that could affect safety

## Prompt: Medication Reconciliation

**Input components:**
- Patient demographics and clinical context (age, conditions, labs)
- Array of conflicting medication records with source metadata
- Pre-computed evidence weights from Phase 1

**Output structure:**
- `reconciled_medication`: The recommended medication, dose, and frequency
- `confidence_score`: 0.0 to 1.0 float
- `reasoning`: 2-3 sentence clinical justification
- `recommended_actions`: Array of specific follow-up actions
- `clinical_safety_check`: PASSED / WARNING / FAILED
- `safety_details`: Explanation for non-PASSED results

**Why this structure works:**
The reasoning field forces the model to articulate its clinical logic, which serves both as an explanation for clinicians and as a quality signal (incoherent reasoning suggests low confidence in the result). The recommended_actions field transforms the reconciliation from a passive report into actionable guidance.

## Prompt: Data Quality Assessment

**Input components:**
- Full patient record (demographics, medications, allergies, conditions, vitals)
- Issues already detected by rule-based validators

**Output structure:**
- `additional_issues`: Array of issues the rules missed
- `clinical_observations`: Summary of overall clinical plausibility

**Why this structure works:**
The prompt explicitly tells the model which issues have already been detected and instructs it not to repeat them. This prevents duplicate findings and focuses the model on the harder clinical observations: drug-disease mismatches, expected-but-missing data (e.g., no glucose monitoring for a diabetic patient), and suspiciously empty allergy lists.

## Error Handling in Prompt Design

The LLM client includes a JSON parser with fallback logic:

1. Attempt to parse the response as raw JSON
2. If that fails, strip markdown code fences and retry
3. If both fail, return the raw text in a wrapper dict with a `parse_error` flag

This three-tier parsing handles the known failure modes of LLM JSON output without crashing the application.

## Iteration Notes

The prompts were developed through iterative testing against the 7 sample reconciliation scenarios. Key refinements:

- Added the explicit "no markdown" instruction after early responses included ```json fences
- Added the safety check as a numbered step after noticing the model would skip it when it was only mentioned in the output template
- Added the evidence weights section after observing that without quantitative priors, the model would sometimes override clear recency signals with speculative clinical reasoning
