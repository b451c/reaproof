"""Unit tests for the audio signal + analysis library (no REAPER)."""
import numpy as np
import pytest

from reaproof.observe.audio import analysis as A
from reaproof.observe.audio import signals as S


def test_sine_rms_is_peak_minus_3db():
    x = S.sine(1000, dbfs=-12.0, seconds=1.0)
    # a sine's RMS is 3.01 dB below its peak
    assert A.approx_dbfs(A.rms_dbfs(x), -15.01, tol_db=0.05, why="sine RMS=peak-3.01dB")
    assert A.approx_dbfs(A.peak_dbfs(x), -12.0, tol_db=0.05, why="declared peak level")


def test_silence_is_floor():
    assert A.rms_dbfs(S.silence(0.5)) < -200


def test_true_peak_ge_sample_peak():
    x = S.sine(997, dbfs=-1.0, seconds=0.5)  # near-fs sine: inter-sample > sample peak
    assert A.true_peak_dbfs(x) >= A.peak_dbfs(x) - 1e-6


def test_null_identical_is_floor_and_scaled_is_audible():
    x = S.sine(1000, dbfs=-12.0, seconds=1.0)
    assert A.null_test_dbfs(x, x.copy()) < -200          # bit-identical
    half = x * 0.5                                        # residual = 0.5x, RMS ~ -21 dBFS
    assert A.null_test_dbfs(x, half) > -30               # clearly non-null (vs -120 floor)


def test_lufs_matches_expectation():
    x = S.sine(1000, dbfs=-12.0, seconds=2.0)
    assert A.approx_dbfs(A.lufs_integrated(x, 48000), -12.0, tol_db=1.0,
                         why="BS.1770 LUFS of a -12dBFS sine ~ -12 LUFS")


def test_spectral_centroid_tracks_frequency():
    lo = A.spectral_centroid(S.sine(500, seconds=0.5), 48000)
    hi = A.spectral_centroid(S.sine(5000, seconds=0.5), 48000)
    assert hi > lo * 2  # centroid rises with the tone


# ---- pathology detectors (hard fail) ----
def test_nan_and_inf_are_pathologies():
    x = S.sine(1000, seconds=0.2)
    x[10, 0] = np.nan
    with pytest.raises(A.AudioPathology):
        A.assert_no_pathology(x)
    y = S.sine(1000, seconds=0.2)
    y[10, 0] = np.inf
    with pytest.raises(A.AudioPathology):
        A.assert_no_pathology(y)


def test_clean_signal_has_no_pathology():
    A.assert_no_pathology(S.sine(1000, seconds=0.5))  # no raise


def test_dc_offset_detected():
    x = S.sine(1000, seconds=0.5) + 0.2
    kinds = {p.kind for p in A.detect_pathologies(x)}
    assert "dc_offset" in kinds


def test_click_detected():
    x = S.sine(1000, seconds=0.5)
    x[1000, :] += 1.0  # a discontinuity
    kinds = {p.kind for p in A.detect_pathologies(x)}
    assert "click" in kinds
