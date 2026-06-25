"""Vision glitch scan (§9.5) — advisory, additive heuristics.

Catches classes of breakage a golden diff can miss across legitimate theme changes:
all-black / all-white / blank frames, frozen/degenerate renders, extreme uniformity.
Per the doctrine this check can RAISE a failure but a green vision check never
OVERRIDES a pixel/state disagreement. (A multimodal model can be layered on top; the
heuristics here are deterministic and dependency-free.)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Glitch:
    kind: str
    detail: str


class FrameGlitch(AssertionError):
    pass


def glitch_scan(img: np.ndarray, *, min_std: float = 3.0,
                min_unique_colors: int = 8) -> list[Glitch]:
    a = np.asarray(img)[..., :3]
    out: list[Glitch] = []
    mean = a.mean()
    std = float(a.std())
    if std < min_std:
        out.append(Glitch("blank", f"near-uniform frame (std={std:.2f})"))
    if mean < 4:
        out.append(Glitch("all_black", f"mean={mean:.2f}"))
    if mean > 251:
        out.append(Glitch("all_white", f"mean={mean:.2f}"))
    uniq = len(np.unique(a.reshape(-1, 3), axis=0))
    if uniq < min_unique_colors:
        out.append(Glitch("degenerate", f"only {uniq} unique colors"))
    return out


def assert_no_glitch(img: np.ndarray, **kw) -> None:
    g = glitch_scan(img, **kw)
    if g:
        raise FrameGlitch("; ".join(f"{x.kind}: {x.detail}" for x in g))
