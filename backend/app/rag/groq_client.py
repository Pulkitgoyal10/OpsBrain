"""Groq LLM client with multi-model fallback, per CLAUDE.md B4."""

from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv
from groq import APIError, Groq, RateLimitError

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

# llama-3.1-8b-instant is the primary model - verified extensively (Phase
# 3.5) to be accurate and non-hallucinating on our grounded-answer prompt.
# openai/gpt-oss-20b is the fallback: a different model family/lineage (not
# another Llama variant) so a shared failure mode is less likely, similar
# size class to what's proven to work. Both confirmed live via
# client.models.list() rather than assumed from memory. Deliberately not
# using llama-3.3-70b-versatile here even though it's available - that's the
# model Phase 3.5 moved away from after it hallucinated/looped on dense
# tabular context, so it's a poor choice even as a fallback.
FALLBACK_MODELS = ["llama-3.1-8b-instant", "openai/gpt-oss-20b"]

# Free-tier is tokens-per-minute limited (6000 TPM). Batch callers (e.g. the
# graph extractor, one call per prose chunk) blow past it, so retry on 429
# with a backoff long enough for the per-minute window to refill, before
# falling back to the next model. This is the free-tier resilience lesson
# from CLAUDE.md, applied to Groq.
_MAX_RETRIES = 6
_BACKOFF_SECONDS = [5, 10, 15, 20, 30, 30]

class GroqRateLimitExhausted(RuntimeError):
    """Every fallback model was still rate-limited after exhausting retries.

    Distinct from the generic RuntimeError below so callers (app.main's
    /chat handler) can surface a specific "try again in a moment" message
    instead of a raw exception string for this one recoverable case.
    """


_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.environ["GROQ_API_KEY"]
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set in backend/.env")
        # max_retries=0: we own the retry loop below so backoff is long enough
        # for the TPM window, rather than the SDK's short default backoff.
        _client = Groq(api_key=api_key, max_retries=0)
    return _client


def chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    frequency_penalty: float = 0.0,
    response_format: dict | None = None,
    models: list[str] = FALLBACK_MODELS,
) -> str:
    client = _get_client()
    kwargs: dict = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "frequency_penalty": frequency_penalty,
    }
    # Pass response_format only when set, so JSON mode is opt-in (Groq requires
    # the word "json" in the prompt when {"type": "json_object"} is used).
    if response_format is not None:
        kwargs["response_format"] = response_format

    last_error: Exception | None = None

    for model in models:
        for attempt in range(_MAX_RETRIES):
            try:
                response = client.chat.completions.create(model=model, **kwargs)
                return response.choices[0].message.content
            except RateLimitError as e:
                # Retry the *same* model first - a 429 is often a transient
                # TPM-window issue that resolves within a few seconds, so it's
                # worth waiting before burning the fallback.
                last_error = e
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS[attempt])
            except APIError as e:
                # Any other API error (auth, bad request, server error, ...)
                # won't be fixed by retrying the same model - move on.
                last_error = e
                break

    if isinstance(last_error, RateLimitError):
        raise GroqRateLimitExhausted(
            f"All Groq models rate-limited. Last error: {last_error}"
        ) from last_error

    raise RuntimeError(f"All Groq models failed. Last error: {last_error}") from last_error
