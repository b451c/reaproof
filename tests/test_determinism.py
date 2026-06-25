"""Tests for the determinism lock + repeat/compare helpers (§1.4, §5.1)."""
import pytest

from reaproof.determinism import (
    LOCKED_ENV,
    DeterminismLock,
    NonDeterminismError,
    assert_identical,
    sha256_bytes,
    subprocess_env,
)


def test_locked_env_pins_numeric_locale():
    # the comma-decimal hazard (§5.1) must be neutralised for everything we spawn
    assert LOCKED_ENV["LC_NUMERIC"] == "C"
    assert LOCKED_ENV["TZ"] == "UTC"
    env = subprocess_env({"EXTRA": "1"})
    assert env["LC_NUMERIC"] == "C" and env["EXTRA"] == "1"


def test_determinism_lock_is_recorded():
    lock = DeterminismLock()
    d = lock.as_dict()
    for k in ("sample_rate", "block_size", "dpi", "seed", "locale", "timezone", "software_render"):
        assert k in d
    assert d["sample_rate"] == 48000 and d["seed"] == 0x5EED


def test_assert_identical_accepts_equal_runs():
    assert_identical([{"a": 1}, {"a": 1}], what="state")  # no raise


def test_assert_identical_flags_disagreement():
    with pytest.raises(NonDeterminismError):
        assert_identical([{"a": 1}, {"a": 2}], what="state")


def test_assert_identical_needs_two_runs():
    with pytest.raises(ValueError):
        assert_identical([{"a": 1}])


def test_sha256_bytes_stable():
    assert sha256_bytes(b"reaproof") == sha256_bytes(b"reaproof")
    assert sha256_bytes(b"a") != sha256_bytes(b"b")
