# ReaProof

**Trustworthy, automated testing for REAPER plugins and extensions - point it at a plugin and get a real pass/fail, no test code required.**

ReaProof loads your compiled plugin into a clean, isolated REAPER instance, renders real
audio through it, reads the real project state, and asserts on the *observable effect* -
never on a mocked or simulated value. It is built around one rule:

> **A green result must mean the plugin genuinely works. A red result must mean something
> genuinely broke. The tool must never produce a false result.**

Every check is *mutation-verified*: before a check is trusted, ReaProof proves it can
actually fail (e.g. by injecting a NaN, or perturbing the signal). A check that cannot be
made to fail is reported as vacuous, not green.

---

## Quickstart

```bash
pip install -e .

# point ReaProof at your REAPER (or provision the pinned one - see Setup)
export REAPROOF_REAPER_APP=/Applications/REAPER.app

reaproof doctor                              # check the environment
reaproof test /path/to/MyPlugin.clap         # run the universal battery - no test code
open .cache/runs/autotest-MyPlugin/report.html
```

That's it. You do **not** write any test code to get a real, trustworthy QA gate.

## What `reaproof test` checks (zero code)

Point it at a `.clap`, `.vst3`, or `.vst` and it runs a universal battery, all derived
from the plugin itself:

| Check | What it proves |
|---|---|
| **Validator** | Conformance via clap-validator / pluginval, crash-isolated in its own process. |
| **Load** | The plugin actually instantiates inside REAPER (not just "links"). |
| **Audio integrity** | Renders of silence / sine / noise / impulse / sweep / full-scale are free of NaN, Inf, denormals (and clicks/DC on tones). |
| **Determinism** | A re-render is bit-for-bit identical - no hidden nondeterminism. |
| **Parameter robustness** | *Every* parameter is swept across its whole range; the output must stay pathology-free at every setting (catches blow-ups, NaN-at-extreme-settings, instability). |

```
ReaProof - universal test of MyPlugin.clap

  [PASSED   ] validator: clap-validator conformance - 11/21 passed, 0 failed
  [PASSED   ] load: plugin instantiates in REAPER - fx=0 nparams=12
  [PASSED   ] audio: silence is pathology-free
  [PASSED   ] audio: silence in -> silence out
  [PASSED   ] audio: sine_1k is pathology-free
  [PASSED   ] audio: noise is pathology-free
  [PASSED   ] audio: impulse is pathology-free
  [PASSED   ] audio: sweep is pathology-free
  [PASSED   ] audio: fullscale_sine is pathology-free
  [PASSED   ] determinism: re-render is bit-identical
  [PASSED   ] param sweep [0] Cutoff: stable across range
  ...
GREEN OK  passed 18 - failed 0 - skipped 0
```

Useful flags: `--full` (sweep all parameters, no cap), `--no-sweep`, `--instrument`
(a generator: skip the silence-in/silence-out check), `--out DIR`.

## Optional: semantic ("does it do the right thing") tests

The universal battery proves a plugin loads, is stable, deterministic, and never emits
garbage. To additionally assert *what* it should do - "this gain is exactly -6 dB", "the
reverb tail grows with the size knob", "the knob's drawn angle matches its value" - you
write a short spec with the authoring API. These are a few lines each, mutation-verified
the same way. See [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) and the worked example in
[`examples/`](examples/). This is optional; the universal battery needs nothing but the plugin.

## How it works

```
reaproof test <plugin>
      |
      v
 Provisioner  ->  isolated REAPER (your install, hermetic profile, never touches your config)
      |
      +-- Validator         (clap-validator / pluginval, out-of-process)
      +-- Control bridge     (in-REAPER Lua, file-queue IPC + heartbeat for hang detection)
      +-- Observation:
           - audio: offline render -> NumPy/SciPy analysis (RMS, true-peak, LUFS, spectrum, pathologies)
           - state: read back a different way than you set it
           - visual: capture the real plugin window + dual-channel cross-check (needs js_ReaScriptAPI)
      |
      v
 Report (JUnit + JSON + HTML) with a provenance manifest - independently reproducible
```

Compiled plugins are loaded the way REAPER really loads them (`TrackFX_AddByName`), with
the subject exposed to REAPER hermetically (CLAP via the standard `CLAP_PATH`), and their
parameters driven by automation envelopes so the value actually takes effect at render time.

## Setup

**Requirements**

- **macOS** (Apple Silicon or Intel). Linux is CI-verified for the audio/bridge planes; Windows is not supported yet.
- **REAPER 7.x** - point `REAPROOF_REAPER_APP` at it, or provision a pinned copy under `.cache/` for cross-machine determinism.
- **Python 3.11+** with the dependencies in `pyproject.toml` (`pip install -e .`).
- **Optional validators**: [clap-validator](https://github.com/free-audio/clap-validator) and/or [pluginval](https://github.com/Tracktion/pluginval). Missing ones are skipped, not failed.
- **Optional REAPER extensions** for the visual/input planes: js_ReaScriptAPI, SWS, ReaImGui. The audio battery does not need them.

Run `reaproof doctor` to see exactly what is present. Anything missing is reported with a hint.

**Notes**

- ReaProof assembles a throwaway, isolated REAPER profile per run; it never reads or writes your real REAPER configuration or plugins.
- The first run may scan your installed plugins (REAPER does this at startup). If a plugin on your system hangs REAPER's scanner, see [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) (Troubleshooting).
- macOS visual tests require Screen Recording permission for your terminal.

## CLI

```
reaproof doctor                 # environment health
reaproof test <plugin> [opts]   # universal zero-code battery
reaproof run [paths] [opts]     # run a folder of custom pytest specs (report + repeat + mutation-check)
reaproof goldens list|approve   # review/approve reference images (never auto-updated)
```

## Project layout

```
src/reaproof/        the platform (provision, control, observe/{audio,visual,input}, validators, report, runner)
bridge/              the in-REAPER Lua control bridge
examples/            reference subjects (a JSFX gain knob, a CLAP gain) + a worked custom test
tests/               the platform's own test suite (it tests itself)
docs/                USER_GUIDE.md, REFERENCE.md
```

## Status

- Audio + validator + load + parameter planes: working on macOS, exercised by the test suite and real plugins.
- Visual capture + dual-channel + input synthesis: working on macOS (and Linux for the in-process path).
- Windows: not yet. Contributions welcome.

## License

MIT - see [LICENSE](LICENSE).
