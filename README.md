# Clinical Data Reconciliation Engine

A hybrid rule-based and AI-powered system for reconciling conflicting medication records across clinical sources. Built for the Onye Full Stack Developer (EHR Integration) assessment.

## What This Does

Healthcare providers frequently encounter conflicting patient data across systems. A hospital EHR might list one medication dose while a pharmacy record shows another. This engine takes those conflicting records, weights them using deterministic clinical rules, then refines the result using AI reasoning to produce a single reconciled recommendation with a confidence score, safety check, and audit trail.

## Quick Start

### Prerequisites

- Python 3.11+
- An Anthropic API key ([get one here](https://console.anthropic.com))

### Local Setup

```bash
# Clone and enter the project
git clone https://github.com/YOUR_USERNAME/clinical-reconciliation-engine.git
cd clinical-reconciliation-engine

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Run the application
uvicorn backend.app.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000) for the dashboard, or [http://localhost:8000/api/docs](http://localhost:8000/api/docs) for the interactive API documentation.

### Docker Setup

```bash
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY
docker compose up --build
```

### Running Tests

```bash
cd backend
python -m pytest tests/ -v
```

## Architecture

### Why a Hybrid Approach (Rule-Based + LLM)

Most approaches to this problem would send conflicting records straight to an LLM and ask it to pick the right one. That works for demos but has real problems in clinical settings: LLM outputs are nondeterministic, reasoning is opaque, and there is no fallback when the API goes down.

This engine uses a two-phase approach:

**Phase 1 (Deterministic Evidence Scoring):** Before the LLM sees anything, a rule-based scorer computes a numerical weight for each source based on four factors:

- **Recency**: Exponential decay with a 90-day half-life. A record updated yesterday carries more weight than one from six months ago.
- **Reliability tier**: High-reliability sources (hospital EHRs, specialist clinics) multiply at 1.0x. Medium (pharmacies) at 0.7x. Low (patient portals) at 0.4x.
- **Clinical context**: Lab values directly affect plausibility. A patient with an eGFR of 42 (declining kidney function) makes a lower Metformin dose more plausible than a higher one.
- **Concordance**: When two or more sources agree on the same medication and dose, they both receive a bonus. Agreement across independent systems is strong evidence.

**Phase 2 (LLM Clinical Reasoning):** The pre-computed weights are fed into the prompt alongside the raw records. The LLM (Claude) refines the analysis with clinical judgment that rules cannot capture: guideline awareness, pharmacokinetic reasoning, drug-interaction checks. The LLM returns a structured JSON response with the reconciled medication, confidence score, reasoning, and safety check.

**Fallback behavior:** If the Anthropic API is unavailable, the engine returns the highest-weighted source from Phase 1 with a discounted confidence score and a warning flag. The system never fails silently.

### Why These Frameworks and Libraries

**FastAPI** (backend framework): Chosen over Flask and Django for three reasons. First, native async support matters for LLM API calls that take 2-5 seconds. FastAPI handles concurrent requests without blocking. Second, Pydantic integration provides automatic request validation and OpenAPI documentation with zero extra code. Third, the auto-generated `/api/docs` endpoint gives evaluators (and future developers) an interactive API explorer for free.

**Pydantic** (data validation): Every request and response is defined as a Pydantic model with field-level constraints, type checking, and custom validators. This catches malformed data at the API boundary before it reaches business logic. In a clinical context, input validation is not optional.

**Anthropic Python SDK** (LLM integration): Selected Claude over OpenAI for this project because Anthropic's structured output consistency is strong for JSON-formatted clinical reasoning, and the SDK provides clean error types (RateLimitError, APIStatusError, APIConnectionError) that map directly to retry strategies.

**Tailwind CSS** (frontend styling): Per the assessment's tech stack requirement. Tailwind via CDN keeps the frontend as a single HTML file with no build step, which simplifies deployment and review. The utility-first approach produces a polished, clinician-friendly interface without custom CSS files.

**Vanilla JavaScript** (frontend logic): No React or Vue. The frontend is two forms, two result panels, and an approve/reject workflow. A framework would add bundle complexity without proportional benefit. Vanilla JS keeps the frontend dependency-free and instantly readable by any reviewer.

**pytest** (testing): Standard Python testing framework. Tests cover the deterministic Phase 1 logic exhaustively (evidence weights, concordance, clinical context adjustments, cache behavior, input validation, fallback paths). The LLM-dependent Phase 2 is tested via integration rather than unit tests, since mocking an LLM's clinical reasoning would not validate anything meaningful.

### Why These Design Decisions

**Content-addressable response cache:** LLM calls cost money and take seconds. Identical reconciliation requests (same patient context, same sources) will always produce the same Phase 1 evidence scores. The cache uses a SHA-256 hash of the serialized request as the key, so repeated submissions return instantly. TTL defaults to 1 hour. In production, this would be Redis.

**FHIR-aware naming without full FHIR compliance:** The models use FHIR-inspired field names (MedicationStatement, PatientContext) and structure, but do not implement the full FHIR R4 specification. Full FHIR compliance would require a FHIR server, resource validation, and reference resolution that are outside this assessment's scope. The naming convention signals awareness of the standard and makes future FHIR migration straightforward.

**API key authentication over OAuth2/JWT:** The assessment requires "basic authentication/API key protection." A simple X-API-Key header check is implemented. The README and code comments note that production deployment would require OAuth2 or JWT with proper token rotation.

**Single-process serving (API + static files):** FastAPI serves both the REST API and the frontend static files from one process. This eliminates CORS complexity, simplifies deployment, and means `docker compose up` gives you the complete application. A production deployment would separate these behind a reverse proxy.

## API Endpoints

### POST /api/reconcile/medication

Reconciles conflicting medication records from multiple clinical sources.

**Headers:** `X-API-Key: your-api-key`

**Request body:** See `backend/sample_data/test_scenarios.json` for complete examples.

### POST /api/validate/data-quality

Scores a patient record across completeness, accuracy, timeliness, and clinical plausibility.

**Headers:** `X-API-Key: your-api-key`

### GET /health

Returns system status including LLM request count and cache sizes.

### GET /api/docs

Interactive Swagger UI for testing both endpoints.

## Project Structure

```
clinical-reconciliation-engine/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entry point, CORS, router registration
│   │   ├── config.py            # Environment-based configuration
│   │   ├── auth.py              # API key authentication
│   │   ├── models/
│   │   │   ├── medication.py    # Reconciliation request/response models
│   │   │   └── data_quality.py  # Data quality request/response models
│   │   ├── services/
│   │   │   ├── reconciliation.py # Hybrid reconciliation engine (Phase 1 + 2)
│   │   │   ├── data_quality.py   # Four-dimension quality scorer
│   │   │   ├── llm_client.py     # Anthropic API wrapper with retries
│   │   │   ├── prompts.py        # Documented prompt engineering module
│   │   │   └── cache.py          # Content-addressable response cache
│   │   └── routers/
│   │       ├── reconcile.py      # /api/reconcile/medication endpoint
│   │       ├── validate.py       # /api/validate/data-quality endpoint
│   │       └── health.py         # /health endpoint
│   ├── tests/
│   │   └── test_core.py          # 25+ unit tests across 7 test classes
│   └── sample_data/
│       └── test_scenarios.json   # 7 reconciliation + 3 quality scenarios
├── frontend/
│   ├── index.html                # Dashboard (Tailwind CSS)
│   └── js/
│       └── app.js                # Frontend logic (vanilla JS)
├── docs/
│   ├── ARCHITECTURE.md           # Detailed architecture decisions
│   └── PROMPT_ENGINEERING.md     # Prompt design documentation
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

## LLM Choice: Anthropic Claude

**Why Claude over OpenAI or open-source models:**

1. **Structured output reliability**: Claude consistently returns well-formed JSON when instructed, which is critical for a system that parses LLM output programmatically. In testing, Claude's JSON compliance rate exceeded what I observed with GPT-4o for similar clinical prompts.

2. **Clinical reasoning depth**: The assessment requires generating human-readable clinical reasoning. Claude produces explanations that reference specific clinical factors (lab values, guideline recommendations, pharmacokinetic principles) rather than generic summaries.

3. **Error handling ergonomics**: The Anthropic Python SDK surfaces typed exceptions (RateLimitError, APIStatusError, APIConnectionError) that map cleanly to retry strategies. This made implementing the exponential backoff logic straightforward.

4. **Cost efficiency**: For an application that caches responses and uses a rule-based pre-filter, the per-request cost is manageable. The hybrid architecture means the LLM is doing refinement rather than heavy lifting, so shorter prompts and responses keep token usage low.

## What I Would Improve With More Time

1. **Persistent storage**: Replace in-memory cache with Redis. Add a PostgreSQL or SQLite database for audit trail persistence (reconciliation history, approval/rejection log).

2. **FHIR resource parsing**: Accept raw FHIR Bundles as input and extract MedicationStatement, Patient, and Observation resources automatically. This would make the engine directly compatible with OnyeSync's FHIR pipeline.

3. **Confidence score calibration**: The current confidence score comes from the LLM's self-assessment. A calibrated approach would track historical accuracy (reconciled medication vs. confirmed medication after manual review) and adjust scores using logistic regression.

4. **Duplicate record detection**: Implement fuzzy matching (Levenshtein distance + medication name normalization via RxNorm) to detect when multiple sources are describing the same prescription differently vs. genuinely different medications.

5. **WebSocket updates**: Replace the request-response pattern with WebSocket connections so the frontend can show real-time progress during LLM processing.

6. **End-to-end integration tests**: Add tests that hit the actual API endpoints with httpx.AsyncClient to validate the full request lifecycle including authentication, validation, caching, and response formatting.

## Estimated Time Spent

~12 hours across architecture design, backend implementation, frontend, testing, and documentation.

## Test Data

The `backend/sample_data/test_scenarios.json` file contains 7 medication reconciliation scenarios and 3 data quality scenarios. These cover: renal dosing adjustments, complete source disagreement, anticoagulant safety, concordance detection, specialist vs. generalist conflicts, stale record handling, and pediatric dosing. The data quality scenarios cover implausible vitals, incomplete records, and well-documented records as a control.
