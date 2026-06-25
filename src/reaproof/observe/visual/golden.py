"""Golden image management + approval workflow (§9.4).

Goldens are keyed by ``(os, dpi, theme, plugin, version, control, state)`` so a
Windows-150%-dark golden is a different artifact from a Linux-100%-light one. A
changed render NEVER auto-updates the golden: ``compare`` reports the diff, and a
human (or a gated agent step) must ``approve`` it, with the approver + reason stored
in provenance. Silent golden updates would destroy the value of visual regression.
"""
from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from reaproof import paths
from reaproof.observe.visual import diff as _diff


@dataclass(frozen=True)
class GoldenKey:
    plugin: str
    version: str
    control: str
    state: str           # e.g. "gain=-6db" or "default"
    os: str = platform.system().lower()
    dpi: int = 100
    theme: str = "default"

    def slug(self) -> str:
        safe = lambda s: str(s).replace(" ", "_").replace("/", "-").replace("=", "")
        return "__".join(safe(x) for x in (
            self.plugin, self.version, self.os, f"dpi{self.dpi}", self.theme,
            self.control, self.state))


@dataclass
class GoldenComparison:
    key: GoldenKey
    exists: bool
    diff: _diff.DiffResult | None
    golden_path: Path
    passed: bool
    note: str = ""


class GoldenStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else paths.GOLDENS

    def path(self, key: GoldenKey) -> Path:
        return self.root / f"{key.slug()}.png"

    def meta_path(self, key: GoldenKey) -> Path:
        return self.root / f"{key.slug()}.json"

    def exists(self, key: GoldenKey) -> bool:
        return self.path(key).exists()

    def compare(self, img: np.ndarray, key: GoldenKey, *, threshold: int = 16,
                max_fraction: float = 0.002) -> GoldenComparison:
        gp = self.path(key)
        if not gp.exists():
            return GoldenComparison(key, False, None, gp, passed=False,
                                    note="no golden yet — needs review/approval")
        golden = np.asarray(Image.open(gp).convert("RGB"), dtype=np.uint8)
        if golden.shape != np.asarray(img).shape:
            d = _diff.DiffResult(False, 1.0, golden.size, 255, 0.0, 999.0)
            return GoldenComparison(key, True, d, gp, passed=False, note="size mismatch")
        d = _diff.compare(golden, np.asarray(img, dtype=np.uint8), threshold=threshold)
        passed = not d.differs(max_fraction=max_fraction)
        return GoldenComparison(key, True, d, gp, passed=passed)

    def approve(self, img: np.ndarray, key: GoldenKey, *, approver: str,
                reason: str) -> Path:
        """Write/replace the golden and record who approved it and why (provenance)."""
        self.root.mkdir(parents=True, exist_ok=True)
        gp = self.path(key)
        Image.fromarray(np.asarray(img, dtype=np.uint8)).save(gp)
        self.meta_path(key).write_text(json.dumps({
            "key": key.__dict__, "approver": approver, "reason": reason,
            "sha256": _diff.image_hash(np.asarray(img, dtype=np.uint8)),
        }, indent=2))
        return gp
