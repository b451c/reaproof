"""Control-coverage taxonomy (REFERENCE §7): control types × test dimensions.

"Complete" is measurable: for each control a plugin exposes, the report lists which
applicable cells are exercised (and each should be mutation-verified). Cells marked
N/A for a control type are excluded from the denominator.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CONTROL_TYPES = [
    "rotary_knob", "slider", "button", "multistate_switch", "dropdown",
    "numeric_field", "xy_pad", "envelope_curve", "meter", "waveform_spectrum",
    "keyboard", "custom_canvas",
]

DIMENSIONS = [
    "functional", "dsp", "automation", "state", "visual_at_value", "redraw",
    "interaction", "dpi", "multimonitor", "theme", "resize", "thread_safety",
    "crash_fuzz",
]

# N/A cells per control type (everything else applies), transcribed from REFERENCE §7.
_NA: dict[str, set[str]] = {
    "dropdown": {"automation"},
    "meter": {"functional", "automation", "state", "interaction"},
    "waveform_spectrum": {"functional", "automation", "state", "interaction"},
    "keyboard": {"state"},
}


def applies(control: str, dimension: str) -> bool:
    if control not in CONTROL_TYPES:
        raise ValueError(f"unknown control type: {control}")
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension: {dimension}")
    return dimension not in _NA.get(control, set())


def applicable_cells(control: str) -> list[str]:
    return [d for d in DIMENSIONS if applies(control, d)]


@dataclass
class ControlCoverage:
    control: str
    covered: list[str]
    uncovered: list[str]

    @property
    def fraction(self) -> float:
        total = len(self.covered) + len(self.uncovered)
        return len(self.covered) / total if total else 1.0


@dataclass
class CoverageReport:
    controls: list[ControlCoverage] = field(default_factory=list)

    @property
    def fraction(self) -> float:
        cov = sum(len(c.covered) for c in self.controls)
        tot = sum(len(c.covered) + len(c.uncovered) for c in self.controls)
        return cov / tot if tot else 1.0

    def gaps(self) -> dict[str, list[str]]:
        return {c.control: c.uncovered for c in self.controls if c.uncovered}

    def to_dict(self) -> dict:
        return {
            "fraction": self.fraction,
            "controls": [
                {"control": c.control, "covered": c.covered, "uncovered": c.uncovered,
                 "fraction": c.fraction}
                for c in self.controls
            ],
            "gaps": self.gaps(),
        }


def coverage_report(exercised: dict[str, set[str]]) -> CoverageReport:
    """``exercised`` maps each control type under test to the dimensions its tests
    exercise. Returns covered vs uncovered (applicable) cells per control."""
    report = CoverageReport()
    for control, dims in exercised.items():
        applicable = set(applicable_cells(control))
        covered = sorted(d for d in dims if d in applicable)
        uncovered = sorted(applicable - set(covered))
        report.controls.append(ControlCoverage(control, covered, uncovered))
    return report
