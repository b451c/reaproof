"""Knob indicator angle measurement — the pixel side of the dual-channel check (§9.6).

Given a capture of a rotary knob whose indicator is a coloured line from the centre
to the rim (the @gfx convention used by Subject #1), recover the indicator angle from
pixels and map it to the control's value. Comparing this against the value the plugin
*reports* is what makes a visual test both precise and meaningful: a logic/draw
divergence (engine says one thing, GUI draws another) is caught as a disagreement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class KnobReading:
    angle_deg: float          # 0 = pointing up, clockwise positive (the @gfx convention)
    value: float              # angle mapped through the declared range
    n_pixels: int
    base_xy: tuple[float, float]
    tip_xy: tuple[float, float]


def indicator_mask(img: np.ndarray, rgb=(235, 184, 51), tol=70) -> np.ndarray:
    """Pixels close to the indicator colour (default the knob's yellow)."""
    a = img.astype(np.int32)
    r, g, b = rgb
    return (np.abs(a[:, :, 0] - r) < tol) & (np.abs(a[:, :, 1] - g) < tol) & (a[:, :, 2] < b + tol)


def measure_indicator_angle(
    img: np.ndarray, *, rgb=(235, 184, 51), tol=70, min_pixels=20,
    chrome_top_frac: float = 0.33, region: tuple[int, int, int, int] | None = None,
) -> KnobReading:
    """Recover the indicator angle from all indicator-coloured pixels in the knob
    region. The window chrome (title/preset/slider) above ``chrome_top_frac`` is
    excluded so its stray same-colour pixels don't bias the line fit; within the
    knob area the indicator is the only such colour, so a robust PCA over every
    pixel beats fragile connected-component logic on a thin diagonal line."""
    mask = indicator_mask(img, rgb, tol)
    if region is not None:
        keep = np.zeros_like(mask)
        l, t, r, b = region
        keep[t:b, l:r] = True
        mask &= keep
    elif chrome_top_frac > 0:
        mask[: int(img.shape[0] * chrome_top_frac), :] = False
    ys, xs = np.where(mask)
    if len(xs) < min_pixels:
        raise ValueError(f"indicator not found ({len(xs)} px < {min_pixels}) — "
                         "missing/garbled knob (a real visual defect)")
    pts = np.column_stack([xs, ys]).astype(np.float64)
    centred = pts - pts.mean(axis=0)
    # principal axis of the indicator line via PCA
    _, _, vh = np.linalg.svd(centred, full_matrices=False)
    axis = vh[0]
    proj = centred @ axis
    a_pt = pts[proj.argmin()]
    b_pt = pts[proj.argmax()]
    # the TIP carries the filled dot => denser pixels nearby; the BASE is the knob centre
    def density(p):
        return int(((xs - p[0]) ** 2 + (ys - p[1]) ** 2 < 7 ** 2).sum())
    if density(a_pt) >= density(b_pt):
        tip, base = a_pt, b_pt
    else:
        tip, base = b_pt, a_pt
    # @gfx convention: ix-cx = sin(ang)*L, cy-iy = cos(ang)*L (y grows downward)
    ang = math.degrees(math.atan2(tip[0] - base[0], base[1] - tip[1]))
    return KnobReading(angle_deg=ang, value=float("nan"), n_pixels=len(xs),
                       base_xy=(float(base[0]), float(base[1])),
                       tip_xy=(float(tip[0]), float(tip[1])))


def angle_to_value(angle_deg: float, vmin: float, vmax: float,
                   angle_min: float = -135.0, angle_max: float = 135.0) -> float:
    frac = (angle_deg - angle_min) / (angle_max - angle_min)
    return vmin + frac * (vmax - vmin)


def measure_knob_value(img: np.ndarray, *, vmin: float, vmax: float,
                       rgb=(235, 184, 51), tol=70, angle_span=(-135.0, 135.0),
                       chrome_top_frac: float = 0.33,
                       region: tuple[int, int, int, int] | None = None) -> KnobReading:
    r = measure_indicator_angle(img, rgb=rgb, tol=tol,
                                chrome_top_frac=chrome_top_frac, region=region)
    r.value = angle_to_value(r.angle_deg, vmin, vmax, *angle_span)
    return r
