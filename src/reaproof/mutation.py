"""Mutation engine (§1.3) — the platform's defence against vacuous green.

For each assertion the harness applies a controlled perturbation that SHOULD break
it (multiply audio by 0.5, offset a knob, shift a golden by 2 px, swap a broken
build) and confirms the assertion turns RED. An assertion that stays green under
its mutation is VACUOUS and FAILS the suite. This is a first-class feature, not an
afterthought: a green test that never proved it could fail has not earned trust.

An "assertion" here is any callable that RAISES on failure (e.g. an ``assert`` body
or a pytest check) and returns normally on success.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

Assertion = Callable[[Any], None]
Mutation = Callable[[Any], Any]

# Tests that proved an assertion non-vacuous (populated by raise_if_vacuous). The
# pytest plugin reads this to annotate each result's mutation status (§1.3).
MUTATION_VERIFIED: set[str] = set()


def _record_verified() -> None:
    cur = os.environ.get("PYTEST_CURRENT_TEST", "")
    if cur:
        MUTATION_VERIFIED.add(cur.split(" ", 1)[0])  # nodeid, drop the "(call)" suffix


@dataclass
class MutationOutcome:
    name: str
    killed: bool          # True iff the assertion turned RED under this mutation
    detail: str = ""


@dataclass
class MutationReport:
    clean_passed: bool
    outcomes: list[MutationOutcome] = field(default_factory=list)

    @property
    def vacuous(self) -> bool:
        # Vacuous if the clean assertion didn't pass, or ANY mutation failed to kill it.
        return (not self.clean_passed) or any(not o.killed for o in self.outcomes)

    def raise_if_vacuous(self) -> None:
        if not self.clean_passed:
            raise VacuousAssertion("the assertion did not pass on the clean subject")
        dead = [o.name for o in self.outcomes if not o.killed]
        if dead:
            raise VacuousAssertion(
                "VACUOUS assertion — survived mutation(s): " + ", ".join(dead)
            )
        _record_verified()  # this test proved an assertion non-vacuous (§1.3)


class VacuousAssertion(AssertionError):
    """The assertion could not be made to fail by a mutation that should break it."""


def mutation_check(
    subject: Any,
    assert_fn: Assertion,
    mutations: list[tuple[str, Mutation]],
) -> MutationReport:
    """Run ``assert_fn`` clean (must pass) then under each mutation (must fail)."""
    try:
        assert_fn(subject)
        clean_passed = True
    except AssertionError:
        clean_passed = False

    outcomes: list[MutationOutcome] = []
    for name, mut in mutations:
        mutated = mut(subject)
        try:
            assert_fn(mutated)
            outcomes.append(MutationOutcome(name, killed=False,
                                            detail="assertion stayed GREEN (vacuous)"))
        except AssertionError as e:
            outcomes.append(MutationOutcome(name, killed=True, detail=str(e)[:160]))
    return MutationReport(clean_passed, outcomes)


# ---- common mutations on audio sample arrays ------------------------------
def scale(factor: float) -> Mutation:
    """Multiply samples by a factor (e.g. 0.5 => -6 dB). The canonical audio mutation."""
    return lambda x: np.asarray(x, dtype=np.float64) * factor


def add_dc(level: float) -> Mutation:
    return lambda x: np.asarray(x, dtype=np.float64) + level


def offset_value(delta: float) -> Mutation:
    """Perturb a scalar measurement by delta (for numeric assertions)."""
    return lambda v: v + delta


def inject_nan() -> Mutation:
    def _m(x):
        y = np.asarray(x, dtype=np.float64).copy()
        if y.size:
            y.flat[0] = np.nan
        return y
    return _m
