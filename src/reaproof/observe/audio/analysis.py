"""Audio analysis (§8.3): the numerical ground truth for DSP correctness.

Measurements (RMS / peak / true-peak / LUFS / spectral), the null test, and the
pathology detectors that map NaN/Inf/denormal/DC/clicks to a HARD FAIL (§1.7).
Tolerances are explicit and justified at each call site (§1.6).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_FLOOR = 1e-30  # avoids log(0); ~ -600 dBFS, far below any real signal


def _mono(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return x.mean(axis=1) if x.ndim > 1 else x


def rms_dbfs(x: np.ndarray) -> float:
    m = _mono(x)
    return float(20.0 * np.log10(np.sqrt(np.mean(m**2)) + _FLOOR))


def peak_dbfs(x: np.ndarray) -> float:
    m = _mono(x)
    return float(20.0 * np.log10(np.max(np.abs(m)) + _FLOOR))


def true_peak_dbfs(x: np.ndarray, sr: int = 48000, oversample: int = 4) -> float:
    """Inter-sample (true) peak via polyphase oversampling."""
    from scipy.signal import resample_poly

    m = _mono(x)
    up = resample_poly(m, oversample, 1)
    return float(20.0 * np.log10(np.max(np.abs(up)) + _FLOOR))


def lufs_integrated(x: np.ndarray, sr: int = 48000) -> float:
    """Integrated loudness (ITU-R BS.1770) via pyloudnorm.

    BS.1770 gating needs at least one 400 ms block; below that pyloudnorm
    silently yields -inf/degenerate values. Enforced here so a too-short
    render is a loud error, not a silently wrong loudness.
    """
    import pyloudnorm as pyln

    data = np.asarray(x, dtype=np.float64)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    if data.shape[0] < int(0.4 * sr):
        raise ValueError(
            f"integrated LUFS needs >= 400 ms of audio (got {data.shape[0]/sr*1000:.0f} ms)")
    meter = pyln.Meter(sr)
    return float(meter.integrated_loudness(data))


def spectral_centroid(x: np.ndarray, sr: int = 48000) -> float:
    """Energy-weighted mean frequency (Hz) — e.g. to verify a cutoff knob moves it.

    Silence/empty input returns NaN (unmeasurable), NOT 0.0 — a broken plugin
    outputting silence must not masquerade as a genuine low-frequency reading.
    Any comparison against NaN is False, so an assertion fails loudly.
    """
    m = _mono(x)
    if m.size == 0:
        return float("nan")
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    freqs = np.fft.rfftfreq(len(m), 1.0 / sr)
    denom = spec.sum()
    return float((freqs * spec).sum() / denom) if denom > 0 else float("nan")


def rms_envelope_dbfs(x: np.ndarray, sr: int = 48000, window_ms: float = 50.0) -> np.ndarray:
    """Short-time RMS (dBFS) per non-overlapping window — the output's level over time,
    for verifying that automation tracks the envelope (§8.3)."""
    m = _mono(x)
    w = max(1, int(sr * window_ms / 1000.0))
    n = len(m) // w
    if n == 0:
        return np.array([rms_dbfs(m)])
    blocks = m[: n * w].reshape(n, w)
    return 20.0 * np.log10(np.sqrt((blocks**2).mean(axis=1)) + _FLOOR)


def null_test_dbfs(a: np.ndarray, b: np.ndarray) -> float:
    """Residual level of (a - b) — the strongest audio assertion (§8.3).

    Bit-identical inputs give -inf (clamped to the floor). Caller asserts the
    residual is below a justified floor (e.g. <= -120 dBFS for "transparent").

    STRICT on length: a null over only the overlapping prefix would let a
    truncated render pass as "transparent" while its missing tail was never
    examined — a false PASS. Unequal lengths raise; align/trim explicitly at
    the call site if a length difference is expected.
    """
    ma, mb = _mono(a), _mono(b)
    if len(ma) != len(mb):
        raise ValueError(
            f"null test requires equal lengths, got {len(ma)} vs {len(mb)} — "
            "a truncated render must fail loudly, not null on the prefix")
    if len(ma) == 0:
        raise ValueError("null test on empty signals proves nothing")
    return rms_dbfs(ma - mb)


# ---- pathology detection (hard fail, §1.7) --------------------------------
@dataclass
class Pathology:
    kind: str
    detail: str


class AudioPathology(AssertionError):
    """Raised on a non-finite/denormal/DC/click pathology — never skipped."""


def detect_pathologies(
    x: np.ndarray,
    *,
    dc_thresh: float = 1e-3,      # |mean| above this = real DC offset (not dither)
    click_thresh: float = 0.5,     # |Δsample| jump that signals a discontinuity
    denormal_thresh: float = 1e-30,
) -> list[Pathology]:
    found: list[Pathology] = []
    arr = np.asarray(x, dtype=np.float64)
    if arr.size == 0:
        # an absent/zero-length signal must never read as "pathology-free":
        # a render that produced no audio is a hard failure, not a clean pass
        found.append(Pathology("empty", "no samples to check (render produced nothing?)"))
        return found
    if np.isnan(arr).any():
        found.append(Pathology("nan", f"{int(np.isnan(arr).sum())} NaN samples"))
    if np.isinf(arr).any():
        found.append(Pathology("inf", f"{int(np.isinf(arr).sum())} Inf samples"))
    m = _mono(arr)
    finite = m[np.isfinite(m)]
    if finite.size:
        if abs(float(finite.mean())) > dc_thresh:
            found.append(Pathology("dc_offset", f"mean={float(finite.mean()):.2e}"))
        # denormal storm: many sub-denormal nonzero samples persisting
        sub = (np.abs(finite) > 0) & (np.abs(finite) < denormal_thresh)
        if sub.sum() > finite.size * 0.01:
            found.append(Pathology("denormal", f"{int(sub.sum())} sub-denormal samples"))
        d = np.abs(np.diff(finite))
        if d.size and float(d.max()) > click_thresh:
            found.append(Pathology("click", f"max |Δ|={float(d.max()):.3f} at {int(d.argmax())}"))
    return found


def assert_no_pathology(x: np.ndarray, *, hard_only: bool = False) -> None:
    """Raise AudioPathology if any pathology is present (§1.7). Never returns a skip."""
    issues = detect_pathologies(x)
    if hard_only:
        issues = [i for i in issues if i.kind in ("nan", "inf", "denormal")]
    if issues:
        raise AudioPathology("; ".join(f"{i.kind}: {i.detail}" for i in issues))


# ---- tolerance helper (forces a justification, §1.6) ----------------------
def approx_dbfs(measured: float, expected: float, *, tol_db: float, why: str) -> bool:
    """True iff |measured-expected| <= tol_db. `why` documents the tolerance."""
    assert tol_db > 0, f"tolerance must be positive (why={why})"
    return abs(measured - expected) <= tol_db
