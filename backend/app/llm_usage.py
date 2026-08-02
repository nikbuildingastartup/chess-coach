"""Cost-tracking helper for LLM calls made via fal.ai's OpenAI-compatible
endpoint.

Sibling to `app.llm_json`: both `app.coaching` and `app.focus` share this
one recording helper rather than each rolling their own so pricing
constants and the recording/error-handling behavior stay in exactly one
place.
"""

import logging
from datetime import datetime, timezone
from typing import Literal

from sqlmodel import Session

from app.models import LlmUsage

logger = logging.getLogger(__name__)

INPUT_COST_PER_TOKEN_USD = 1.00 / 1_000_000
OUTPUT_COST_PER_TOKEN_USD = 5.00 / 1_000_000


def record_llm_usage(
    session: Session,
    call_site: Literal["coaching", "focus"],
    model: str,
    usage: object | None,
) -> None:
    """Persist one LlmUsage row from an OpenAI-compatible `response.usage`.

    Never raises -- recording usage is a purely optional side effect of
    a successful LLM call and must not turn a working coaching/focus
    response into a failed one.
    """
    if usage is None:
        return
    try:
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", None) or (
            prompt_tokens + completion_tokens
        )
        input_cost = prompt_tokens * INPUT_COST_PER_TOKEN_USD
        output_cost = completion_tokens * OUTPUT_COST_PER_TOKEN_USD

        session.add(
            LlmUsage(
                created_at=datetime.now(timezone.utc),
                call_site=call_site,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                input_cost_usd=input_cost,
                output_cost_usd=output_cost,
                total_cost_usd=input_cost + output_cost,
            )
        )
        session.commit()
    except Exception:
        logger.exception("Failed to record LLM usage for call_site=%s.", call_site)
        session.rollback()
