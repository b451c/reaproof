"""Shared result type for validator wrappers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidatorResult:
    tool: str
    target: str
    passed: bool
    exit_code: int
    total: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    failed_tests: list[str] = field(default_factory=list)
    log_path: str | None = None
    json_path: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        return (f"{self.tool} {verdict} [{self.target}] "
                f"{self.passed_count}/{self.total} passed, {self.failed_count} failed, "
                f"{self.skipped_count} skipped (exit {self.exit_code})")
