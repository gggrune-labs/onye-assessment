"""
Anthropic Claude API client with rate limiting, retries, and error handling.

Wraps the Anthropic Python SDK with:
- Exponential backoff on rate limit (429) and server (5xx) errors
- Configurable timeout per request
- Structured JSON response parsing with fallback
- Request counting for observability
"""

import json
import logging
import time
from typing import Optional

import anthropic

from ..config import get_settings

logger = logging.getLogger(__name__)


class LLMClientError(Exception):
    """Raised when the LLM client encounters an unrecoverable error."""
    pass


class LLMClient:
    def __init__(self):
        self._settings = get_settings()
        self._client: Optional[anthropic.Anthropic] = None
        self._request_count = 0
        self._last_request_time = 0.0
        self._min_interval = 60.0 / max(self._settings.llm_requests_per_minute, 1)

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not self._settings.anthropic_api_key:
                raise LLMClientError(
                    "ANTHROPIC_API_KEY not configured. Set it in .env or environment."
                )
            self._client = anthropic.Anthropic(
                api_key=self._settings.anthropic_api_key,
                timeout=self._settings.anthropic_timeout,
            )
        return self._client

    def complete(self, prompt: str, max_retries: int = 3) -> dict:
        """
        Send a prompt to Claude and return the parsed JSON response.

        Uses exponential backoff on retryable errors (rate limits, server errors).
        Falls back to raw text wrapped in a dict if JSON parsing fails.
        """
        self._enforce_rate_limit()

        last_error = None
        for attempt in range(max_retries):
            try:
                message = self.client.messages.create(
                    model=self._settings.anthropic_model,
                    max_tokens=self._settings.anthropic_max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                self._request_count += 1
                self._last_request_time = time.time()

                raw_text = message.content[0].text.strip()
                return self._parse_response(raw_text)

            except anthropic.RateLimitError as e:
                last_error = e
                wait = (2 ** attempt) * 2
                logger.warning(f"Rate limited. Retrying in {wait}s (attempt {attempt + 1})")
                time.sleep(wait)

            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    last_error = e
                    wait = (2 ** attempt) * 1
                    logger.warning(f"Server error {e.status_code}. Retrying in {wait}s")
                    time.sleep(wait)
                else:
                    raise LLMClientError(f"Anthropic API error: {e.status_code} {e.message}")

            except anthropic.APIConnectionError as e:
                last_error = e
                wait = (2 ** attempt) * 1
                logger.warning(f"Connection error. Retrying in {wait}s")
                time.sleep(wait)

        raise LLMClientError(f"Failed after {max_retries} retries. Last error: {last_error}")

    def _enforce_rate_limit(self) -> None:
        """Simple token-bucket rate limiter."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    @staticmethod
    def _parse_response(raw: str) -> dict:
        """
        Parse the LLM response as JSON.
        Strips markdown code fences if present.
        """
        cleaned = raw
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last lines (the fences)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM response as JSON. Returning raw.")
            return {"raw_response": raw, "parse_error": True}

    @property
    def request_count(self) -> int:
        return self._request_count


# Module-level singleton
llm_client = LLMClient()
