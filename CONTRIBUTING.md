# Contributing to ReaProof

Thanks for your interest. ReaProof has one overriding principle, and contributions are
judged against it first:

> **No false results.** A green check must mean the thing genuinely works; a red check must
> mean something genuinely broke. Assert on the observable effect (rendered audio, captured
> pixels, state read back a different way), never on the value you set. Every check must be
> mutation-verified - proven able to fail.

## Development setup

```bash
pip install -e .
export REAPROOF_REAPER_APP=/Applications/REAPER.app   # or provision the pinned REAPER
make doctor          # check the environment
make selftest        # fast suite, no REAPER
make selftest-full   # full suite (launches REAPER)
```

The package runs from `src/` (`PYTHONPATH=src`). Tests live in `tests/`; the platform tests
itself. Reference subjects (a JSFX gain knob, a CLAP gain, and intentionally broken variants)
live in `examples/` - they exist so every gate has a passing case *and* a failing negative
control.

## Ground rules

- Keep components small, each with its own tests.
- A new check is not done until it has a **negative control**: something that makes it go red.
- Do not weaken or delete a test to make a gate pass.
- Subprocesses that touch REAPER/analysis run with `LC_NUMERIC=C`, `LC_ALL=en_US.UTF-8`, `TZ=UTC`.
- Determinism matters: a flaky test is quarantined, never retried-to-green.

## Good first contributions

- A Windows provisioner/launcher (the remaining platform gap).
- More validator integrations or signal/metric coverage in the universal battery.
- Additional reference subjects (more control types) under `examples/`.

Open an issue to discuss anything substantial before a large PR. By contributing you agree
your work is licensed under the project's [MIT License](LICENSE).
