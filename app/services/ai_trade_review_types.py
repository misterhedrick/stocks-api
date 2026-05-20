from __future__ import annotations

from dataclasses import dataclass, field

from app.db.models import JobRun, StrategyChangeSuggestion

LOCAL_REVIEW_MODEL = "local-review-v1"
CLAUDE_REVIEW_MODEL = "claude-haiku-4-5-20251001"

@dataclass(slots=True)
class AiTradeReviewWriterResult:
    job_run: JobRun
    trade_cases_seen: int
    reviews_created: int
    reviews_skipped: int
    suggestions_created: int
    errors: list[str] = field(default_factory=list)

@dataclass(slots=True)
class SuggestionReviewResult:
    suggestion: StrategyChangeSuggestion
