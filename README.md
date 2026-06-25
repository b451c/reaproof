# ReaProof

![platform: macOS](https://img.shields.io/badge/platform-macOS-informational)
![Windows / Linux: WIP](https://img.shields.io/badge/Windows%20%2F%20Linux-WIP-lightgrey)
![license: MIT](https://img.shields.io/badge/license-MIT-green)

> **Platform: macOS today.** On Linux the platform-independent layers (trust machinery, audio
> analysis) run in CI; the REAPER-driven planes are implemented (`provision/linux.py`) but not
> yet verified there. **Windows: not yet.**

**Trustworthy, automated testing for anything you build for REAPER - compiled plugins, native extensions, JSFX, and ReaScripts - by driving a real REAPER and asserting on the observable effect.**

ReaProof loads your subject into a clean, isolated REAPER instance, drives it the way a user
would, renders real audio, captures real pixels from the real window, reads the real project
state, and asserts on what actually happened - never on a mocked or simulated value. It is
built around one rule:

> **A green result must mean it genuinely works. A red result must mean something genuinely
> broke. The tool must never produce a false result.**

Every check is *mutation-verified*: before a check is trusted, ReaProof proves it can actually
fail (e.g. by injecting a NaN, or perturbing the signal). A check that cannot be made to fail
is reported as vacuous, not green.

---

## Quickstart (zero-code, for a compiled audio plugin)

```bash
pip install -e .
export REAPROOF_REAPER_APP=/Applications/REAPER.app   # point at your REAPER
reaproof doctor                                        # check the environment
reaproof test /path/to/MyPlugin.clap                   # universal battery - no test code
open .cache/runs/autotest-MyPlugin/report.html
```

For a compiled audio plugin you write **no test code at all** to get a real QA gate.

## What you can test

ReaProof is not only an audio-plugin tester. It can drive **any** REAPER subject and assert on
the real effect. The "zero-code" universal battery applies to compiled audio plugins (they have
a standard shape: parameters + audio I/O that ReaProof can auto-discover and exercise).
Everything else does arbitrary things, so it needs a **short spec** - a few lines saying *what*
should be true after you drive it - written with the authoring API. All of it uses the same
trust machinery (assert-on-effect, mutation-verified, deterministic).

| Subject | How ReaProof tests it | Zero-code? |
|---|---|---|
| **Compiled audio plugin** - CLAP, VST3, VST2 | `reaproof test <plugin>`: validator, load, pathology-free audio, determinism, full parameter-range sweep | ✅ **yes** |
| **JSFX** (REAPER's text DSP) | render audio through it + analyse; dual-channel knob/control checks. Reference subjects ship in `examples/jsfx/` | short spec |
| **Native extension** (`reaper_*.dylib` / `.so` / `.dll`) | load checkpoint + action registration, then drive its actions and assert on the resulting project state / rendered audio | short spec |
| **ReaScript** - Lua / EEL (and Python, if enabled) | run the script or its action through the in-REAPER bridge, then assert on the observable effect (state, items, audio, `@gfx` pixels) | short spec |
| **Audio Unit (AU)** | conformance via `auval` (out-of-process) | validator only |

A short spec looks like this (drive the subject, then assert on the effect, read back a
different way than you set it):

```python
from reaproof.runner.session import ReaperSession

with ReaperSession() as s:
    # ... set up project state, then run the subject (an action id, a script, an FX) ...
    s.eval("reaper.Main_OnCommand(reaper.NamedCommandLookup('_MY_SCRIPT'), 0)")
    # assert on the OBSERVABLE effect
    markers = s.eval("return reaper.CountProjectMarkers(0)")
    assert markers == 1, "the script did not place exactly one marker"
```

The authoring API also gives one-liners for the common audio/visual cases
(`assert_gain_db`, `assert_state_roundtrip`, `knob_editor(...).assert_dual_channel(...)`).
See [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) and [`examples/`](examples/).

## What `reaproof test` checks (the zero-code battery)

Point it at a `.clap`, `.vst3`, or `.vst` and it runs a universal battery, all derived from
the plugin itself - no knowledge of what the plugin "does" is required:

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
  [PASSED   ] audio: sine_1k / noise / impulse / sweep / fullscale_sine - pathology-free
  [PASSED   ] determinism: re-render is bit-identical
  [PASSED   ] param sweep [0..N] every parameter stable across range
GREEN OK  passed 18 - failed 0 - skipped 0
```

Flags: `--full` (sweep all parameters, no cap), `--no-sweep`, `--instrument` (a generator:
skip the silence-in/silence-out check), `--out DIR`.

## What ReaProof observes (the four planes)

The reason it can assert on real behaviour is that it observes the subject four independent ways:

- **Audio** - offline `-renderproject` to real samples, analysed with NumPy/SciPy: RMS, peak,
  true-peak, LUFS, spectral centroid, null tests, and pathology detection (NaN / Inf / denormal
  storms / DC offset / clicks). Crash, hang, or NaN is a hard fail, never a skip.
- **Visual** - capture the real plugin/JSFX window pixels and compare with a tiered diff
  (exact hash -> perceptual -> SSIM/deltaE), with versioned **goldens** and an explicit approval
  step (never auto-updated), plus a glitch check for black/blank frames. **Dual-channel**: a
  value-bearing control (e.g. a rotary knob) is cross-checked by measuring its *drawn* angle from
  pixels and comparing it to the value the plugin *reports* - catching a GUI that lies about its
  engine, or vice versa.
- **State** - set a value one way, read it back a *different* way (through the saved project
  chunk / a reload), so you test real persistence, not the variable you just wrote.
- **Input** - synthesise real gestures (mouse drag / click / wheel) against the real window, to
  test that dragging a control actually moves the value *and* the drawn pixels.

## What makes a green trustworthy

- **Assert on the effect, never on the value you set** (rendered audio, captured pixels, state read back a different way).
- **Mutation-verification** - every check is proven able to fail; a check that cannot be killed is reported vacuous, not green.
- **Determinism + quarantine** - each gate runs more than once; if results disagree the test is *quarantined* (surfaced, excluded from green), never retried-to-green.
- **Validators** run out-of-process so a plugin crash becomes a result, not a tool failure.
- **Provenance** - every result carries a manifest (OS, REAPER build, sample rate, tool versions, input/output hashes) and artifacts, so any result is independently reproducible.
- **Reports** - JUnit (CI), JSON (machines), HTML (humans), with the mutation status visible per check.

## How it works

```
reaproof test <plugin>   |   a custom spec (pytest)   |   the agentic build loop calling either
        |
        v
 Provisioner  ->  isolated REAPER (your install, hermetic profile; never touches your real config)
        |
        +-- Validator         (clap-validator / pluginval / auval, out-of-process)
        +-- Control bridge     (in-REAPER Lua, file-queue IPC + heartbeat for hang detection)
        +-- Observation        (audio render+analysis / visual capture+dual-channel / state / input)
        |
        v
 Report (JUnit + JSON + HTML) + provenance manifest
```

Compiled plugins are loaded the way REAPER really loads them (`TrackFX_AddByName`); the subject is
exposed hermetically (CLAP via the standard `CLAP_PATH`), and parameters are driven by automation
envelopes so the value actually takes effect at render time. Extensions are installed into the
isolated profile's `UserPlugins` and driven through their registered actions. ReaProof is a
**verification layer**: in an agent-driven build loop it provides the trustworthy green/red and
the detailed report that the agent (or you) acts on to correct the code; it does not read or
compile your source - it tests the built subject by running it for real.

## Setup

**Requirements**

- **macOS** (Apple Silicon or Intel) - the only fully supported platform today. On Linux the platform-independent layers (trust machinery, audio analysis) are CI-tested; the REAPER-driven planes are implemented (`provision/linux.py`) but **not yet CI-verified**. Windows is **not supported yet**.
- **REAPER 7.x** - point `REAPROOF_REAPER_APP` at your install, or provision a pinned copy under `.cache/` for cross-machine determinism.
- **Python 3.11+** (`pip install -e .`).
- **Optional validators**: [clap-validator](https://github.com/free-audio/clap-validator), [pluginval](https://github.com/Tracktion/pluginval), `auval` (system). Missing ones are skipped, not failed.
- **Optional REAPER extensions** for the visual/input planes: js_ReaScriptAPI, SWS, ReaImGui. The audio battery does not need them.

Run `reaproof doctor` to see exactly what is present; anything missing is reported with a hint.
ReaProof assembles a throwaway, isolated REAPER profile per run and never reads or writes your
real REAPER configuration or plugins. macOS visual tests need Screen Recording permission for
your terminal. See [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) for the worked tutorials and troubleshooting.

## CLI

```
reaproof doctor                 # environment health
reaproof test <plugin> [opts]   # universal zero-code battery (compiled audio plugins)
reaproof run [paths] [opts]     # run custom pytest specs (report + repeat/quarantine + mutation-check)
reaproof init <dir>             # scaffold a tests folder
reaproof new-test [opts]        # generate a test from a template
reaproof goldens list|approve   # review/approve reference images (never auto-updated)
```

## Project layout

```
src/reaproof/        the platform: provision/ control/ observe/{audio,visual,input}/ validators/
                     coverage/ report/ runner/{autotest,cli,pytest_plugin,quarantine}/ authoring · mutation · determinism
bridge/              the in-REAPER Lua control bridge
examples/            reference subjects (JSFX gain knob + broken variants, a CLAP gain) + a worked custom test
tests/               the platform's own test suite (it tests itself)
docs/                USER_GUIDE.md, REFERENCE.md
```

## Status

- **macOS**: fully supported - audio + validator + load + parameter planes and visual capture + dual-channel + input synthesis, exercised by the suite and real plugins (CLAP/VST3/VST2; JSFX via the audio machinery; extensions and ReaScripts driven through the bridge).
- **Linux**: the platform-independent layers are CI-tested; the REAPER-driven planes (`provision/linux.py`, in-process visual/input) are implemented but not yet CI-verified.
- **Windows**: not yet.
- Contributions welcome - see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT - see [LICENSE](LICENSE).
