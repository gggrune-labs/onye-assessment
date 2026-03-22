# Architecture Decisions

## Decision 1: Hybrid Rule-Based + LLM Pipeline

**Context**: The assessment requires using an LLM for clinical reasoning. A pure LLM approach would send raw conflicting records to Claude and return whatever it generates.

**Decision**: Implement a two-phase pipeline where deterministic rules run first, then the LLM refines the result using the pre-computed evidence as a prior.

**Rationale**: Clinical medication reconciliation has real patient safety implications. A deterministic first pass ensures the system's behavior is auditable and predictable. The rule-based evidence weights (recency decay, reliability tiers, clinical context, concordance) provide a quantitative foundation that the LLM enhances rather than replaces. This also provides a graceful degradation path: if the LLM API is down, the system still returns a reasonable result with a reduced confidence score and a warning flag.

**Trade-off**: More complex than a single LLM call. Worth it for reliability and auditability.

## Decision 2: Single-Process Architecture

**Context**: The application has a Python backend API and an HTML/JS frontend.

**Decision**: Serve both from a single FastAPI process. The frontend is mounted as static files, and the API routes sit alongside them.

**Rationale**: For an assessment submission, eliminating deployment complexity matters. One `uvicorn` command (or `docker compose up`) starts everything. No separate frontend dev server, no CORS configuration needed, no multi-container orchestration. The trade-off is that a production deployment would separate static serving (via CDN or nginx) from the API server.

## Decision 3: In-Memory Cache with Content Addressing

**Context**: LLM API calls cost money and add 2-5 seconds of latency. Identical reconciliation requests should not repeat this work.

**Decision**: Implement a content-addressable cache using SHA-256 hashes of serialized request bodies as keys.

**Rationale**: Content addressing means the cache key is deterministic and collision-resistant without needing to define "what makes two requests the same" manually. The cache is in-memory with TTL expiry and LRU eviction. For this assessment, in-memory is sufficient. The cache interface is simple enough that swapping to Redis requires changing only the `ResponseCache` class internals.

## Decision 4: Pydantic Models as the Schema Contract

**Context**: The assessment provides example JSON structures for both endpoints.

**Decision**: Define strict Pydantic models for all request and response bodies, with field-level validators, type constraints, and default values.

**Rationale**: In a clinical data system, accepting malformed input silently is dangerous. Pydantic rejects invalid data at the API boundary with descriptive error messages. It also auto-generates the OpenAPI schema that powers the interactive docs at `/api/docs`. The models serve triple duty: validation, documentation, and serialization.

## Decision 5: Prompt Structure with Evidence Priors

**Context**: The LLM needs enough context to make clinically sound recommendations but should not reason entirely from scratch.

**Decision**: Include the pre-computed evidence weights in the prompt so the LLM has a quantitative prior to work with.

**Rationale**: Asking an LLM to weigh recency, reliability, and clinical factors from scratch on every call introduces unnecessary variance. By providing the rule-based scores, the prompt essentially says "our analysis suggests Source A is strongest; refine this with your clinical knowledge." This produces more consistent, higher-quality outputs and makes the LLM's job focused on clinical judgment rather than arithmetic.

## Decision 6: Exponential Backoff with Typed Error Handling

**Context**: The Anthropic API can return rate limit errors (429), server errors (5xx), and connection failures.

**Decision**: Implement a retry loop with exponential backoff that distinguishes between retryable and non-retryable errors using the SDK's typed exception hierarchy.

**Rationale**: Rate limits and transient server errors are expected in production. The retry logic uses increasing delays (2s, 4s, 8s for rate limits; 1s, 2s, 4s for server errors) to respect the API's backpressure signals. Non-retryable errors (400-level client errors) fail immediately with a descriptive message rather than wasting retry attempts.

## Decision 7: Vanilla JS Frontend

**Context**: Assessment requires a frontend dashboard. Tech stack specifies Tailwind CSS but does not mandate a framework.

**Decision**: Use vanilla JavaScript with Tailwind via CDN. No React, Vue, or build tooling.

**Rationale**: The frontend has two forms, two result panels, an approve/reject interaction, and a review log. This is well within what vanilla JS handles cleanly. Adding React would require a build step (Vite or CRA), a node_modules directory, and framework-specific patterns that add complexity without proportional benefit for this scope. The result is a single HTML file and a single JS file that any developer can read without framework knowledge.
