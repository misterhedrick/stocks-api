from __future__ import annotations

from dataclasses import dataclass
from typing import Any

class OptionContractSelectionError(RuntimeError):
    pass


class OptionContractNotFoundError(LookupError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


@dataclass(slots=True)
class CandidateRejection:
    symbol: str | None
    reason_code: str
    reason: str
    details: dict[str, Any]


