"""ReaProof — autonomous, trustworthy testing platform for REAPER.

Public API surfaces as phases land. The prime directive (no false results) is
enforced in code: see ``reaproof.mutation`` (mutation-verification),
``reaproof.determinism`` (env lock + repeat/compare) and the dual-channel visual
checks in ``reaproof.observe.visual``.
"""

__version__ = "0.0.1"
