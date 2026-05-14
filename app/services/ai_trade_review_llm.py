from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from app.core.config import settings
from app.services.ai_trade_review_prompt import build_trade_review_prompt

logger = logging.getLogger(__name__)

_VALID_SIGNAL_QUALITY = {"good", "questionable", "poor", "unclear"}
_VALID_EXIT_QUALITY = {"good", "questionable", "poor", "unclear"}
_VALID_OVERALL = {"positive", "negative", "mixed", "neutral"}


def call_claude_trade_review(
    assessment: dict[str, Any],
    *,
    model: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call Claude to review a single trade case assessment.

    Returns (enriched_assessment, raw_response). On parse failure the
    enriched_assessment falls back to the original with an error note appended.
    Raises on network / auth errors so the writer can record them.
    """
    from app.services.ai_trade_review_prompt import _SYSTEM_PROMPT

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    user_prompt = build_trade_review_prompt(assessment)

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = message.content[0].text if message.content else ""
    raw_response = {
        "source": "claude_llm_review",
        "review_model": model,
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
        "stop_reason": message.stop_reason,
        "raw_text": raw_text,
    }

    parsed = _parse_llm_response(raw_text)
    enriched = {**assessment, **parsed, "review_status": "llm_reviewed"}
    return enriched, raw_response


def _parse_llm_response(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    # Strip markdown code fences if present.
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Claude response was not valid JSON: %s", exc)
        return {"llm_parse_error": str(exc), "llm_raw_text": raw_text}

    result: dict[str, Any] = {}

    outcome_assessment = data.get("outcome_assessment")
    if isinstance(outcome_assessment, str):
        result["outcome_assessment"] = outcome_assessment

    signal_quality = data.get("signal_quality")
    if signal_quality in _VALID_SIGNAL_QUALITY:
        result["signal_quality"] = signal_quality
    result["signal_quality_notes"] = data.get("signal_quality_notes") or ""

    exit_quality = data.get("exit_quality")
    if exit_quality in _VALID_EXIT_QUALITY:
        result["exit_quality"] = exit_quality
    result["exit_quality_notes"] = data.get("exit_quality_notes") or ""

    key_observations = data.get("key_observations")
    if isinstance(key_observations, list):
        result["key_observations"] = [str(item) for item in key_observations if item]

    recommendations = data.get("recommendations")
    if isinstance(recommendations, list):
        result["recommendations"] = [
            _coerce_recommendation(r)
            for r in recommendations
            if isinstance(r, dict)
        ]

    overall = data.get("overall_assessment")
    if overall in _VALID_OVERALL:
        result["overall_assessment"] = overall

    return result


def _coerce_recommendation(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": str(r.get("type") or "general"),
        "description": str(r.get("description") or ""),
        "human_review_required": True,
    }
