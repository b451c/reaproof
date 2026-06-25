"""Tiered image diff (§9.3): exact hash -> perceptual -> structural (SSIM / ΔE).

Thresholds are meant to be *calibrated by the mutation check* (§1.3): the smallest
intended visual change (e.g. a 1° knob rotation) must diff above threshold while
sub-pixel anti-aliasing noise must not.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np


@dataclass
class DiffResult:
    exact: bool
    changed_fraction: float       # fraction of pixels beyond the perceptual threshold
    changed_pixels: int
    max_channel_delta: int
    ssim: float
    mean_delta_e: float

    def differs(self, *, max_fraction: float) -> bool:
        return not self.exact and self.changed_fraction > max_fraction


def image_hash(img: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(img, dtype=np.uint8).tobytes()).hexdigest()


def exact_equal(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and bool(np.array_equal(a, b))


def perceptual_diff(a: np.ndarray, b: np.ndarray, *, threshold: int = 16):
    """Per-pixel max-channel difference; counts pixels whose change exceeds
    ``threshold`` (AA-aware: sub-threshold noise is ignored). Returns (count,
    fraction, max_delta, mask)."""
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch {a.shape} vs {b.shape}")
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    mask = d > threshold
    return int(mask.sum()), float(mask.mean()), int(d.max()), mask


def _to_gray(img: np.ndarray) -> np.ndarray:
    return img[..., :3].astype(np.float64) @ np.array([0.299, 0.587, 0.114])


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Global SSIM (single-window approximation) on luma. 1.0 == identical."""
    x, y = _to_gray(a), _to_gray(b)
    if x.shape != y.shape:
        raise ValueError("shape mismatch")
    mx, my = x.mean(), y.mean()
    vx, vy = x.var(), y.var()
    cov = ((x - mx) * (y - my)).mean()
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    return float(((2 * mx * my + c1) * (2 * cov + c2)) /
                 ((mx**2 + my**2 + c1) * (vx + vy + c2)))


def _srgb_to_lab(img: np.ndarray) -> np.ndarray:
    c = img[..., :3].astype(np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = lin @ m.T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    bb = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, bb], axis=-1)


def mean_delta_e(a: np.ndarray, b: np.ndarray) -> float:
    """Mean CIE76 ΔE (Euclidean distance in CIELAB) — perceptual colour difference."""
    la, lb = _srgb_to_lab(a), _srgb_to_lab(b)
    return float(np.sqrt(((la - lb) ** 2).sum(axis=-1)).mean())


def compare(a: np.ndarray, b: np.ndarray, *, threshold: int = 16) -> DiffResult:
    if exact_equal(a, b):
        return DiffResult(True, 0.0, 0, 0, 1.0, 0.0)
    n, frac, mx, _ = perceptual_diff(a, b, threshold=threshold)
    return DiffResult(False, frac, n, mx, ssim(a, b), mean_delta_e(a, b))
