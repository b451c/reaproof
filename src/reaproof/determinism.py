"""Determinism lock (§5.1, §1.4) and provenance helpers.

This module owns the inputs that must be frozen for "same test, same inputs =>
same result" to hold, and the env every subprocess we control inherits. The
matrix varies these *across* runs, never *within* one.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Locale/timezone are frozen: the host uses a comma decimal separator (observed
# `df` -> "1,9Ti"), exactly the §5.1 hazard for value<->text round-trips. Every
# subprocess we directly control (validators, analysis) inherits these. (REAPER
# itself is launched via macOS `open`, which does not propagate env — its own
# number formatting is verified empirically in Phase 1; see DECISIONS D13.)
LOCKED_ENV: dict[str, str] = {
    "LC_ALL": "en_US.UTF-8",
    "LC_NUMERIC": "C",
    "LANG": "en_US.UTF-8",
    "TZ": "UTC",
    # No phone-home from anything we spawn.
    "PYTHONHASHSEED": "0",
}


def subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A clean child environment with the determinism lock applied."""
    env = dict(os.environ)
    env.update(LOCKED_ENV)
    if extra:
        env.update(extra)
    return env


@dataclass(frozen=True)
class DeterminismLock:
    """The pinned, recorded run parameters. Varied by the matrix across runs."""

    sample_rate: int = 48000
    block_size: int = 512
    dpi: int = 100              # 100/150/200; Retina 2x handled per-OS in Phase 5
    theme: str = "default"      # pinned theme id; fonts pinned alongside in Phase 3/5
    seed: int = 0x5EED          # propagated to validators (--seed) and test signals
    locale: str = "en_US.UTF-8"
    timezone: str = "UTC"
    software_render: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_file(path: str | Path, _bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_bufsize):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def host_descriptor() -> dict[str, Any]:
    """Stable description of the host for the provenance manifest."""
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


class NonDeterminismError(AssertionError):
    """Raised when repeated runs disagree beyond tolerance (=> quarantine, §1.4)."""


def assert_identical(values: list[Any], *, what: str = "result") -> None:
    """The §1.4 repeat-compare gate: every repeat must be identical.

    For exact/structural results (state snapshots, hashes). Numeric-with-tolerance
    comparison lives in the audio/visual analysers, which know their tolerances.
    """
    if len(values) < 2:
        raise ValueError("determinism check needs >= 2 runs")
    first = json.dumps(values[0], sort_keys=True, default=str)
    for i, v in enumerate(values[1:], start=2):
        cur = json.dumps(v, sort_keys=True, default=str)
        if cur != first:
            raise NonDeterminismError(
                f"{what} differs between run 1 and run {i} (FLAKY -> quarantine, "
                f"never retry-to-green):\n  run1={first}\n  run{i}={cur}"
            )
