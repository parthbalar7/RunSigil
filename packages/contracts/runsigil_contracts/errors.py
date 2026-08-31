from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    AUTH_REQUIRED = "RUNSIGIL_AUTH_REQUIRED"
    AUTH_INVALID = "RUNSIGIL_AUTH_INVALID"
    SCOPE_DENIED = "RUNSIGIL_SCOPE_DENIED"
    NOT_FOUND = "RUNSIGIL_NOT_FOUND"
    POLICY_UNAVAILABLE = "RUNSIGIL_POLICY_UNAVAILABLE"
    POLICY_DENIED = "RUNSIGIL_POLICY_DENIED"
    BUDGET_EXHAUSTED = "RUNSIGIL_BUDGET_EXHAUSTED"
    APPROVAL_DIGEST_MISMATCH = "RUNSIGIL_APPROVAL_DIGEST_MISMATCH"
    APPROVAL_EXPIRED = "RUNSIGIL_APPROVAL_EXPIRED"
    APPROVAL_REPLAYED = "RUNSIGIL_APPROVAL_REPLAYED"
    INVALID_TRANSITION = "RUNSIGIL_INVALID_TRANSITION"
    ACTION_NOT_AUTHORIZED = "RUNSIGIL_ACTION_NOT_AUTHORIZED"
    ACTION_AMBIGUOUS = "RUNSIGIL_ACTION_AMBIGUOUS"
    EVIDENCE_INVALID = "RUNSIGIL_EVIDENCE_INVALID"
    INTERNAL_DEPENDENCY_UNAVAILABLE = "RUNSIGIL_INTERNAL_DEPENDENCY_UNAVAILABLE"
    VALIDATION_FAILED = "RUNSIGIL_VALIDATION_FAILED"


class RunSigilError(RuntimeError):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
