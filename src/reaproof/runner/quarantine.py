"""Flake quarantine (§1.4, §11.3).

Every test runs >= 2x and must produce identical outcomes. Disagreement => the test
is FLAKY: it is QUARANTINED (reported prominently, excluded from the green/red gate
while remaining visible) and NEVER silently retried until green. Flakiness is a real
defect, treated as one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Verdict(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    QUARANTINED = "quarantined"   # runs disagreed — flaky


@dataclass
class RunOutcome:
    ok: bool
    value: Any = None
    error: str | None = None


@dataclass
class RepeatResult:
    verdict: Verdict
    outcomes: list[RunOutcome]
    reason: str = ""

    @property
    def quarantined(self) -> bool:
        return self.verdict is Verdict.QUARANTINED


def _key(o: RunOutcome) -> str:
    return json.dumps([o.ok, o.value, o.error], sort_keys=True, default=str)


def evaluate(outcomes: list[RunOutcome]) -> RepeatResult:
    if len(outcomes) < 2:
        raise ValueError("quarantine evaluation needs >= 2 runs (§1.4)")
    keys = {_key(o) for o in outcomes}
    if len(keys) > 1:
        return RepeatResult(Verdict.QUARANTINED, outcomes,
                            reason="runs disagreed (FLAKY) — quarantined, not retried to green")
    verdict = Verdict.PASSED if outcomes[0].ok else Verdict.FAILED
    return RepeatResult(verdict, outcomes)


def repeat(fn: Callable[[], Any], n: int = 2) -> RepeatResult:
    """Run ``fn`` n times; an exception is a failed run. Compare outcomes."""
    outcomes: list[RunOutcome] = []
    for _ in range(n):
        try:
            outcomes.append(RunOutcome(ok=True, value=fn()))
        except AssertionError as e:
            outcomes.append(RunOutcome(ok=False, error=str(e)[:200]))
        except Exception as e:  # noqa: BLE001 — any error is a failed run, not a crash of the runner
            outcomes.append(RunOutcome(ok=False, error=f"{type(e).__name__}: {e}"[:200]))
    return evaluate(outcomes)
