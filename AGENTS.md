# Instructions for AI coding agents (Codex, aider, and others)

This repository is **ReaProof** — a no-false-results testing platform for
README and `docs/REFERENCE.md` §8 for the binding doctrine
(mutation-verified assertions, negative controls, determinism ×2, honest
skips — these are enforced mechanically, not aspirational).

## If your task is "write tests for subject X from its source"

Follow `docs/AGENT_TEST_AUTHORING.md` — the complete, tool-agnostic protocol
(feature inventory from source → oracle mapping → specs → mechanical
verification). Entry points are plain CLI:

```
reaproof author <subject> --mode auto|interactive   # scaffold the manifest
reaproof test <subject>                             # zero-code battery first
reaproof test <subject> --quick                     # structural stages, ~10 s -
                                                    # use in your edit loop; run
                                                    # the FULL battery before any
                                                    # done/green claim
reaproof features-report <manifest> --tests <dir>   # anti-fabrication check
```

`--mode auto` = never ask the user; decide conservatively and record every
decision as an assumption in the manifest. `interactive` = ask at the
charter's ⏸ checkpoints.

## Verify before you claim

`make test` runs the full suite (macOS host with the provisioned REAPER —
`make doctor` checks the environment). A change is not done until its gate is
green twice and its negative control fails as specified.
