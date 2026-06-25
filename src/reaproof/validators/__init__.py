"""Industry validator integration (§10) — crash-isolated, out-of-process.

These tools run plugins in separate processes, so a plugin crash becomes a RESULT,
not a platform failure. ReaProof wraps them, feeds the determinism lock (seed),
and folds their output into the unified report with artifacts.
"""
from reaproof.validators.base import ValidatorResult
from reaproof.validators.clap import clap_validator_version, run_clap_validator

__all__ = ["ValidatorResult", "run_clap_validator", "clap_validator_version"]
