"""Feature-manifest engine for the Agent Authoring Harness (U7).

The manifest (``reaproof_features.json``) is the contract between an
AI-authored test suite and reality: every feature the agent found in the
subject's SOURCE, with its oracle mapping and coverage status. This module
validates it mechanically — most importantly the ANTI-FABRICATION rule:
``"covered"`` is refused unless the entry references tests that actually
exist. An agent cannot honestly-or-otherwise mark features covered by prose.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

KINDS = {"param", "action", "ui", "state", "io", "dsp"}
STATUSES = {"covered", "spec-needed", "untestable"}
MODES = {"auto", "interactive"}
_COVERS = re.compile(r"^COVERS\s*=\s*(\[.*?\])", re.S | re.M)


@dataclass
class FeatureReport:
    errors: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    untestable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _covers_declared(test_file: Path) -> list[str]:
    """Feature ids listed in a test file's ``COVERS = [...]`` constant."""
    m = _COVERS.search(test_file.read_text(encoding="utf-8", errors="replace"))
    if not m:
        return []
    try:
        val = json.loads(m.group(1).replace("'", '"'))
        return [str(x) for x in val]
    except json.JSONDecodeError:
        return []


def validate(manifest: dict, *, tests_root: Path | None = None) -> FeatureReport:
    rep = FeatureReport()
    err = rep.errors.append

    mode = manifest.get("mode")
    if mode not in MODES:
        err(f"mode must be one of {sorted(MODES)}, got {mode!r}")
    subject = manifest.get("subject") or {}
    if not subject.get("path"):
        err("subject.path is required")

    features = manifest.get("features")
    if not isinstance(features, list) or not features:
        err("features must be a non-empty list")
        return rep

    seen: set[str] = set()
    for i, f in enumerate(features):
        fid = f.get("id") or f"<features[{i}]>"
        if fid in seen:
            err(f"{fid}: duplicate feature id")
        seen.add(fid)
        if f.get("kind") not in KINDS:
            err(f"{fid}: kind must be one of {sorted(KINDS)}")
        if not f.get("evidence"):
            err(f"{fid}: evidence (source file:line/symbol) is required — "
                "the inventory must be traceable to code")
        status = f.get("status")
        if status not in STATUSES:
            err(f"{fid}: status must be one of {sorted(STATUSES)}")
            continue
        tests = f.get("tests") or []
        if status == "covered":
            # THE anti-fabrication rule: covered without tests is refused
            if not tests:
                err(f"{fid}: status 'covered' with NO test references — "
                    "coverage is proven by tests, not prose")
            elif tests_root is not None:
                for ref in tests:
                    rel = ref.split("::", 1)[0]
                    tf = (tests_root / rel) if not Path(rel).is_absolute() else Path(rel)
                    if not tf.exists():
                        tf2 = Path(rel)
                        if not tf2.exists():
                            err(f"{fid}: referenced test file missing: {rel}")
                            continue
                        tf = tf2
                    declared = _covers_declared(tf)
                    if declared and fid not in declared:
                        err(f"{fid}: {tf.name} has COVERS={declared} which does "
                            f"not include this feature — manifest and test disagree")
        if status == "untestable":
            if not f.get("reason"):
                err(f"{fid}: untestable requires a precise reason (honest skip)")
            rep.untestable.append(f"{fid}: {f.get('reason', '')}")
        if f.get("intent") == "assumed":
            if not f.get("assumption"):
                err(f"{fid}: intent 'assumed' requires the assumption sentence")
            else:
                rep.assumptions.append(f"{fid}: {f['assumption']}")
        rep.counts[status] = rep.counts.get(status, 0) + 1
    return rep


def scaffold(subject: str | Path, *, subject_type: str, mode: str = "interactive",
             source: str | Path | None = None) -> dict:
    """A skeleton manifest for the agent to fill in (one TODO example entry)."""
    return {
        "subject": {"path": str(subject), "type": subject_type,
                    "source": str(source or Path(subject).parent)},
        "mode": mode,
        "features": [{
            "id": "TODO_feature_slug",
            "title": "TODO: one user-meaningful behaviour",
            "kind": "param",
            "evidence": "TODO file.c:123 formula/symbol",
            "oracles": ["audio"],
            "status": "spec-needed",
            "tests": [],
            "intent": "declared",
        }],
    }


def render_report(rep: FeatureReport) -> str:
    lines = []
    c = rep.counts
    lines.append(f"features: covered {c.get('covered', 0)} · "
                 f"spec-needed {c.get('spec-needed', 0)} · "
                 f"untestable {c.get('untestable', 0)}")
    if rep.assumptions:
        lines.append("\nASSUMPTIONS (auto mode decided; review these):")
        lines += [f"  - {a}" for a in rep.assumptions]
    if rep.untestable:
        lines.append("\nUNTESTABLE (honest skips):")
        lines += [f"  - {u}" for u in rep.untestable]
    if rep.errors:
        lines.append("\nMANIFEST ERRORS:")
        lines += [f"  ✗ {e}" for e in rep.errors]
    return "\n".join(lines)
