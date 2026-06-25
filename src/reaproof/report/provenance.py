"""Provenance manifest (§1.8): every result carries enough to reproduce it.

OS, REAPER build, plugin version + hash, sample rate, block size, DPI, theme, seed,
and tool versions. A result a human cannot independently reproduce is not trusted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from reaproof import paths
from reaproof.determinism import DeterminismLock, host_descriptor


@dataclass
class Manifest:
    host: dict[str, Any]
    reaper_build: str
    lock: dict[str, Any]
    tool_versions: dict[str, str]
    plugin: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tool_versions() -> dict[str, str]:
    versions = {"reaper": paths.REAPER_BUILD}
    try:
        from reaproof.validators.clap import clap_validator_version
        versions["clap-validator"] = clap_validator_version()
    except Exception:  # noqa: BLE001 — provenance is best-effort, never fatal
        pass
    try:
        import numpy
        import scipy
        versions["numpy"] = numpy.__version__
        versions["scipy"] = scipy.__version__
    except Exception:  # noqa: BLE001
        pass
    return versions


def build_manifest(lock: DeterminismLock | None = None, *,
                   plugin: dict[str, Any] | None = None,
                   **extra) -> Manifest:
    lock = lock or DeterminismLock()
    return Manifest(
        host=host_descriptor(),
        reaper_build=paths.REAPER_BUILD,
        lock=lock.as_dict(),
        tool_versions=_tool_versions(),
        plugin=plugin or {},
        extra=extra,
    )
