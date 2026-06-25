"""Deterministic test-signal generators (§8.2).

Every signal is a pure function of its arguments (+ an explicit seed for noise), so
the same call yields bit-identical samples — a precondition for §1.4. Returns float
arrays shaped (n_samples, channels).
"""
from __future__ import annotations

import numpy as np


def _db_to_amp(dbfs: float) -> float:
    return float(10.0 ** (dbfs / 20.0))


def _stereo(mono: np.ndarray, channels: int) -> np.ndarray:
    return np.column_stack([mono] * channels) if channels > 1 else mono.reshape(-1, 1)


def sine(freq: float, dbfs: float = -12.0, seconds: float = 2.0,
         sr: int = 48000, channels: int = 2) -> np.ndarray:
    """A full-scale-referenced sine. dbfs is the PEAK level (RMS is 3.01 dB lower)."""
    t = np.arange(int(round(seconds * sr))) / sr
    mono = (_db_to_amp(dbfs) * np.sin(2 * np.pi * freq * t)).astype(np.float64)
    return _stereo(mono, channels)


def silence(seconds: float = 2.0, sr: int = 48000, channels: int = 2) -> np.ndarray:
    return np.zeros((int(round(seconds * sr)), channels), dtype=np.float64)


def dc(level: float = 0.5, seconds: float = 1.0, sr: int = 48000, channels: int = 2) -> np.ndarray:
    return np.full((int(round(seconds * sr)), channels), float(level), dtype=np.float64)


def impulse(seconds: float = 1.0, sr: int = 48000, channels: int = 2,
            position: int = 0, amp: float = 1.0) -> np.ndarray:
    x = np.zeros((int(round(seconds * sr)), channels), dtype=np.float64)
    x[position, :] = amp
    return x


def noise(dbfs: float = -12.0, seconds: float = 2.0, sr: int = 48000,
          channels: int = 2, seed: int = 0x5EED) -> np.ndarray:
    """White noise with a FIXED seed (determinism, §5.1)."""
    rng = np.random.default_rng(seed)
    n = int(round(seconds * sr))
    mono = (_db_to_amp(dbfs) * rng.standard_normal(n)).astype(np.float64)
    return _stereo(mono, channels)


def sweep(f0: float = 20.0, f1: float = 20000.0, dbfs: float = -12.0,
          seconds: float = 3.0, sr: int = 48000, channels: int = 2) -> np.ndarray:
    """Exponential (ESS) sweep — for frequency response + THD."""
    n = int(round(seconds * sr))
    t = np.arange(n) / sr
    k = np.log(f1 / f0)
    phase = 2 * np.pi * f0 * seconds / k * (np.exp(t / seconds * k) - 1.0)
    mono = (_db_to_amp(dbfs) * np.sin(phase)).astype(np.float64)
    return _stereo(mono, channels)
