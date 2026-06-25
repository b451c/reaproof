"""Unit tests for the mutation engine (§1.3) — no REAPER."""
import numpy as np
import pytest

from reaproof.mutation import (
    VacuousAssertion,
    inject_nan,
    mutation_check,
    offset_value,
    scale,
)
from reaproof.observe.audio import analysis as A
from reaproof.observe.audio import signals as S


def test_sensitive_assertion_is_not_vacuous():
    x = S.sine(1000, dbfs=-12.0, seconds=0.5)  # RMS ~ -15.01

    def assert_rms(samples):
        assert A.approx_dbfs(A.rms_dbfs(samples), -15.01, tol_db=0.1, why="test")

    report = mutation_check(x, assert_rms, [("x0.5(-6dB)", scale(0.5))])
    assert report.clean_passed
    assert not report.vacuous
    report.raise_if_vacuous()  # no raise


def test_vacuous_assertion_is_caught():
    x = S.sine(1000, dbfs=-12.0, seconds=0.5)

    # A deliberately too-loose assertion: any RMS below 0 dBFS passes -> can't fail
    def assert_too_loose(samples):
        assert A.rms_dbfs(samples) < 0.0

    report = mutation_check(x, assert_too_loose, [("x0.5", scale(0.5))])
    assert report.clean_passed
    assert report.vacuous
    with pytest.raises(VacuousAssertion):
        report.raise_if_vacuous()


def test_numeric_offset_mutation():
    def assert_eq(v):
        assert abs(v - 0.375) < 1e-6

    report = mutation_check(0.375, assert_eq, [("+1step", offset_value(0.01))])
    assert not report.vacuous


def test_inject_nan_mutation_kills_pathology_assertion():
    x = S.sine(1000, seconds=0.3)
    report = mutation_check(x, A.assert_no_pathology, [("nan", inject_nan())])
    assert report.clean_passed and not report.vacuous
