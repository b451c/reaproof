# ReaProof - User Guide

This guide is for developers of REAPER plugins, extensions, and scripts who want to test
them automatically and *trustworthily* - including the visual behaviour of GUI controls -
without clicking through REAPER by hand every time.

There are two ways to use ReaProof:

1. **Universal, zero-code** - `reaproof test <plugin>` runs a real QA battery (load,
   validator, pathology-free audio, determinism, parameter-range robustness) with no test
   code at all. Start here; it is covered in [the README](../README.md).
2. **Custom semantic specs** - when you want to assert *what* a plugin should do (exact
   gain, a knob's drawn angle, state recall), you write a few lines with the authoring API.
   That is what the rest of this guide covers, with a worked knob tutorial.

> Platform status: verified on **macOS**; the audio/bridge planes are CI-verified on Linux;
> Windows is not supported yet. Visual/input tests need macOS **Screen Recording** permission.

---

## Contents
1. [What you can test](#1-what-you-can-test)
2. [Installation](#2-installation)
3. [Your first test in 10 minutes](#3-your-first-test-in-10-minutes)
4. [The test types](#4-the-test-types)
5. [Tutorial: fully testing a rotary knob](#5-tutorial-fully-testing-a-rotary-knob-functional--visual)
6. [Applying the same rigor to any control](#6-applying-the-same-rigor-to-any-control)
7. [Running in CI](#7-running-in-ci)
8. [Reading the report & trusting results](#8-reading-the-report--trusting-results)
9. [Troubleshooting](#9-troubleshooting)
10. [CLI reference](#10-cli-reference)

---

## 1. What you can test

- **Plugins:** VST2, VST3, CLAP, AU (macOS), LV2 (Linux), JSFX
- **Native extensions:** REAPER C/C++ extensions (actions, API functions)
- **Scripts:** Lua / EEL2 / Python ReaScripts and ReaImGui UIs

And for each, across **Windows, macOS, and Linux**, at multiple **DPI scales** and across **multiple monitors**:

- **Functional** — parameter ranges, value↔text, stepping, defaults
- **DSP** — does the sound actually change the way the control claims (gain, EQ, latency, loudness, distortion, clicks)
- **State** — does it save and reload correctly
- **Visual** — does every control *draw* correctly and *track its value*
- **Interaction** — dragging, scrolling, fine/coarse, double-click-to-default, right-click menus
- **Robustness** — crashes, hangs, NaN, thread-safety, fuzzing

The guarantee that makes this useful: **a green result means it really works, and a red result means it really broke.** ReaProof will not give you a falsely-passing test. See [§8](#8-reading-the-report--trusting-results).

---

## 2. Installation

**Prerequisites:** Python 3.11+. ReaProof downloads and manages its own pinned REAPER and validators, so you do not need REAPER pre-installed (though you can point it at an existing install).

```bash
pip install reaproof          # or: pipx install reaproof (recommended for a CLI tool)

# Download a pinned REAPER + the validators for your OS into ReaProof's cache:
reaproof setup --reaper 7.75
```

`setup` installs/locates: REAPER (portable, isolated profile), pluginval, clap-validator, clap-info, EditorHost, and — on macOS — wires up auval. On Linux it also checks for Xvfb (used to run the real GUI headlessly) and offers to install it.

Verify:

```bash
reaproof doctor
# ✓ REAPER 7.75 (portable, isolated profile)   ✓ js_ReaScriptAPI   ✓ Xvfb
# ✓ pluginval 1.x   ✓ clap-validator x.x   ✓ auval (macOS)   ✓ NumPy/SciPy/soundfile
```

---

## 3. Your first test in 10 minutes

```bash
# Scaffold a tests folder next to your plugin
reaproof init ./my-plugin-tests
cd my-plugin-tests
```

This creates:

```
my-plugin-tests/
  reaproof.toml        # which plugin, formats, OS targets, defaults
  tests/                 # your tests live here
  goldens/               # reference images (created on first approved run)
  conftest.py            # pytest wiring (don't edit unless you know why)
```

Point `reaproof.toml` at your plugin:

```toml
[plugin]
name      = "MyEQ"
formats   = ["VST3", "CLAP"]          # tested for each
path      = "../build"                # where your built plugin(s) are
targets   = ["windows", "macos", "linux"]

[defaults]
sample_rate = 48000
block_size  = 512
dpi_scales  = [100, 150, 200]
strictness  = 5                       # pluginval / clap-validator level
```

Generate a smoke test and run it:

```bash
reaproof new-test --type smoke --plugin "MyEQ"
reaproof run --mutation-check
reaproof report --open
```

A **smoke** test loads the plugin, runs the industry validators, renders silence and a sine, and checks for crashes, NaN, and basic parameter sanity. `--mutation-check` additionally proves each assertion in your suite is capable of failing (so you know none are vacuous).

---

## 4. The test types

You author tests in Python (pytest) using ReaProof fixtures, or generate them with `reaproof new-test --type <type>`. The built-in types:

| Type | What it checks | Generated with |
|---|---|---|
| `smoke` | Loads, validators pass, no crash/NaN, params sane | `--type smoke` |
| `param` | One parameter: range, taper, value↔text, default, step | `--type param --param "Gain"` |
| `dsp` | Audible effect at canonical values; null/spectral/LUFS | `--type dsp --param "Gain" --metric gain_db` |
| `automation` | Sweeps a parameter during render; smoothness, no clicks | `--type automation --param "Cutoff"` |
| `state` | Save → reload in fresh instance → values persist | `--type state` |
| `visual` | A control draws correctly at canonical values (golden) | `--type visual --control "GainKnob"` |
| `interaction` | Drag/wheel/double-click; value *and* pixels track | `--type interaction --control "GainKnob"` |
| `knob` | The full knob matrix (functional + DSP + visual + interaction + DPI + multi-monitor) | `--type knob --control "GainKnob" --param "Gain"` |
| `dpi` | A control at 100/150/200% / Retina; crisp, placed, no clip | `--type dpi --control "GainKnob"` |
| `multimonitor` | Window placement, cross-display drag, per-monitor DPI | `--type multimonitor` |

Anatomy of a test (what the generator writes for you):

```python
from reaproof import session, signals, audio, visual

def test_gain_knob_dsp(reaper):                 # `reaper` fixture = a clean, pinned instance
    fx = reaper.add_plugin("MyEQ", fmt="VST3")  # instantiated on a track, dummy audio
    fx.set_param("Gain", db=-6.0)               # set via host automation path

    reaper.wait_until(fx.param_settled("Gain")) # never sleep()
    out = reaper.render(input=signals.sine(1000, dbfs=-12, seconds=2))

    # Assert on the EFFECT, not the value we set:
    assert audio.rms_dbfs(out) == audio.approx(-18.0, tol_db=0.1)  # -12 input -6 gain
    audio.assert_no_pathology(out)              # NaN/Inf/denormal/clicks → hard fail
```

Everything you assert is automatically eligible for the mutation check.

---

## 5. Tutorial: fully testing a rotary knob (functional + visual)

This is the headline scenario. A knob looks simple, but "fully tested" means a surprising number of things. ReaProof gives you the whole matrix with one generator, then you fill in plugin-specific expectations. Below is the *complete* coverage so you can see nothing is skipped.

```bash
reaproof new-test --type knob --control "GainKnob" --param "Gain" \
    --range "-24,24" --default 0 --units dB --taper linear
```

This generates `tests/test_gainknob.py` covering every cell below. Run it with:

```bash
reaproof run tests/test_gainknob.py --mutation-check --dpi 100,150,200
```

### 5.1 Functional layer (host-visible parameter)

The knob is, to the host, an automatable parameter. ReaProof verifies:

- **Existence & metadata** — the parameter exists, with the expected name, index, units, and is automatable (cross-checked against `clap-info` / the host parameter list).
- **Range & default** — min, max, and default are exactly as declared; reading back a set value returns it (within quantization).
- **Taper** — sweep N points across the range; verify monotonicity, both endpoints, and that the normalized↔plain curve matches the declared taper (linear/log/exp).
- **Value↔text round-trip** — `0.75` formats to the expected text (e.g. `+12.0 dB`), and that text parses back to the same value. (This is a common real bug; clap-validator checks it too.)
- **Stepping/quantization** — for stepped knobs, set values land on exact steps.

### 5.2 DSP layer (the knob actually does what it says)

A "Gain" knob that stores a value but doesn't change the sound is broken. ReaProof renders real audio at canonical values and asserts the **audible effect**:

- At −6 dB, output RMS is 6 dB below the reference render (within 0.1 dB).
- At 0 dB (unity), output **null-tests** against the dry input (residual ≤ −120 dBFS — i.e. truly transparent).
- Monotonic loudness across the sweep; no clipping/true-peak overflow at +24 dB; no NaN/denormal at extreme settings.

(For a cutoff knob it would assert the spectral centroid moves; for wet/dry, the mix ratio via null test — see [§6](#6-applying-the-same-rigor-to-any-control).)

### 5.3 Automation layer

- Render with an automation envelope sweeping the knob over time; verify the output **tracks the envelope sample-accurately** and **smoothly** — no zipper noise, no clicks at control points. (Discontinuity detection is a hard fail.)
- If the plugin supports sample-accurate automation / parameter modulation (CLAP), verify that path too.

### 5.4 State layer

- Set the knob, save the project (and, separately, a plugin preset / FX chunk), reload in a **fresh** REAPER instance, and assert the value persisted exactly.

### 5.5 Visual layer (the knob draws correctly and tracks its value)

This is what you previously had to eyeball. ReaProof captures the **plugin window itself** (DPI-aware, not a full-screen grab) and verifies:

- **It renders** — the editor opens and the knob is present at its expected location.
- **Value → drawn position (dual-channel)** — at value `v`, ReaProof checks **both** that the plugin *reports* `v` **and** that the *drawn indicator points at the angle for* `v`, and requires them to agree. If the engine says −6 dB but the knob is drawn at +6 dB, that's a real bug and the test goes red. This dual check is what makes the visual test both precise and meaningful.
- **Canonical goldens** — min / default / max are compared against approved reference images (per OS/DPI/theme), using exact match where deterministic, else perceptual diff with a threshold *calibrated so a 1° rotation is caught but sub-pixel anti-aliasing noise is not*.
- **Redraw on change** — after changing the value via host/automation, the GUI repaints to match (no stale frame); ReaProof captures before/after.
- **DPI** — the knob is captured at 100/150/200% (and Retina 2× on macOS); it must be crisp, correctly placed, and not clipped or overlapping at any scale.
- **Theme/skin** — if the plugin has skins or follows the host theme, each variant is captured.
- **Resize** — if the editor is resizable, captured at min/mid/max size; layout integrity verified (knob not clipped/overlapping).
- **Glitch scan** — a vision check flags garbled/overlapping/black-frame breakage that golden diffs might miss across legitimate theme changes.

### 5.6 Interaction layer (driving the knob like a user)

ReaProof sends **window-relative synthetic input** (deterministic and headless-safe) and verifies the response on **both** channels (reported value *and* drawn position):

- **Drag** — drag the knob up/down; value and drawn angle track the gesture and land where expected.
- **Fine/coarse** — with Shift (or the plugin's modifier), the same drag distance produces a finer value change.
- **Double-click-to-default** — returns to the declared default (value + visual).
- **Mouse wheel** — increments by the expected step.
- **Right-click** — opens the expected context menu (and, if applicable, "enter value" / "set default").
- **Edge gestures** — dragging past min/max clamps correctly (no wrap, no overshoot) on both channels.

### 5.7 DPI + multi-monitor (explicitly covered)

- Each interaction and visual check is repeated at each DPI scale.
- The editor window is moved to a **second virtual monitor with different DPI**; ReaProof verifies it relocates, redraws crisply, still reports/draws the correct value, and remembers its position when reopened.

### 5.8 Robustness (free, via validators)

In the same run, pluginval/clap-validator exercise the parameter under **fuzzing**, **concurrent thread access** (mimicking simultaneous automation + GUI), **allocation-on-audio-thread** detection, and **state-restoration checksums** — all in a crash-isolated subprocess.

> **Every one of the cells above is mutation-verified.** When you run with `--mutation-check`, ReaProof proves each assertion can fail (e.g. it temporarily desyncs drawn-vs-reported, or shifts the golden, or offsets the value) and confirms the test catches it. Cells that can't fail are flagged VACUOUS. That is your guarantee the suite isn't quietly green.

---

## 6. Applying the same rigor to any control

The knob is the worked example; the same matrix generalises. Use `--type visual/interaction` plus the right metric:

- **Slider / fader** — same as knob; "drawn position" = handle position; DSP metric depends on what it controls.
- **Button / toggle** — visual on/off states; click toggles both reported state and drawn state; DSP/behaviour effect of each state.
- **Multi-state switch / dropdown** — each option selectable; correct option drawn; correct behaviour per option; keyboard navigation.
- **XY pad** — two-axis value↔position; drag tracks both axes; corners/clamping.
- **Envelope / EQ curve editor** — node add/move/delete; the *drawn curve* matches the *parameter set* and the *rendered frequency response* (triple cross-check: nodes → drawn curve → measured spectrum).
- **Meter** — feed known signal; the *drawn fill/level* matches the *measured* level (e.g. a −6 dBFS sine reads −6 on the meter); ballistics (attack/release/peak-hold).
- **Waveform / spectrum display** — feed known content; the drawn display matches the known signal's shape/spectrum.

For any of these, the principle is identical: assert on an independent observation, cross-check pixels against reported state (and, where relevant, against rendered audio), and mutation-verify every assertion.

---

## 7. Running in CI

ReaProof ships GitHub Actions workflows (and works with any CI). The matrix covers OS × format × sample rate × DPI.

```bash
reaproof ci init --provider github
# writes .github/workflows/reaproof.yml
```

What the generated workflow does:

- **Linux job**: runs in a container with REAPER + Xvfb + validators baked in — the real GUI renders into a virtual framebuffer, so visual tests work headlessly. Multi-monitor is simulated with multiple virtual screens.
- **Windows / macOS jobs**: run on runners with a real desktop session (required for GUI). The macOS job additionally verifies plugin **code signing / notarization** (an unsigned plugin failing to load would otherwise look like a test failure).
- **Caching**: REAPER, validators, and goldens are cached.
- **Artifacts**: every run uploads the full evidence bundle (audio, screenshots, diffs, logs, manifest) and the coverage report.
- **Gate**: the build fails on any real failure; **flaky** tests are quarantined and reported but don't silently pass.

Golden images live in your repo (`goldens/`, keyed per OS/DPI/theme). When a render legitimately changes, the diff is surfaced for review — goldens never auto-update silently (`reaproof goldens review` / `approve`).

---

## 8. Reading the report & trusting results

Open the HTML report (`reaproof report --open`). For each test you'll see:

- **Status** — pass / fail / **quarantined (flaky)**.
- **Evidence** — for visual tests, the captured screenshot *annotated with the reported value and the measured drawn value* (the dual-channel check), plus the diff vs golden. For DSP tests, the audio plots and metrics.
- **Mutation result** — confirmation that the assertion was proven able to fail (so you know it's not vacuous). A test with no mutation result hasn't earned your trust yet — run with `--mutation-check`.
- **Provenance** — OS, REAPER build, plugin version + hash, sample rate, block size, DPI, theme, seed, and tool versions, so anyone can reproduce the exact run.

**How ReaProof avoids lying to you (in plain terms):**
- It never checks the value you set — only the *effect* (rendered audio, captured pixels, reloaded state).
- It proves every check can fail before trusting it can pass (mutation check).
- It runs each test twice and treats disagreement as a defect, not something to retry away.
- It waits for the right state instead of sleeping, so timing never fakes a pass or fail.
- It pins everything that affects output, so "it passed on my machine" and "it passed in CI" mean the same thing.
- For visuals, it cross-checks what's *drawn* against what's *reported*, so a pretty-but-wrong (or correct-but-mis-drawn) UI is caught.

---

## 9. Troubleshooting

**"Plugin not found / not scanned."** ReaProof installs to the standard per-OS path and forces a rescan; if your build output is elsewhere, set `plugin.path` in `reaproof.toml`. On macOS, an unsigned/quarantined plugin won't load — `reaproof doctor --signing` checks signature/entitlements.

**"Visual test is flaky."** Almost always fonts, DPI, theme, or GPU rendering not pinned. ReaProof pins these by default; if you overrode a profile, restore the checked-in theme/fonts and ensure software rendering. Flaky tests are quarantined with both runs' images attached so you can see exactly what differed.

**"My knob test passes but I don't believe it."** Run `--mutation-check`. If a cell is reported VACUOUS, the assertion can't fail and needs fixing (usually a too-loose tolerance or asserting on the set value instead of the effect).

**"pluginval fails on my multi-bus VST3."** Known pluginval limitation for some multi-bus VST3 instruments; ReaProof falls back to the Steinberg VST3 validator for those — see the log.

**"Linux CI: GUI tests do nothing."** Ensure the job uses the ReaProof container (with Xvfb) or wrap with `xvfb-run`; on a Wayland host force `XDG_SESSION_TYPE=x11`.

**"Tests are slow."** Use `--skip-gui-tests` for pure-DSP legs, lower `strictness` for quick runs (raise it in nightly), and rely on caching. Visual + interaction legs are the expensive ones; gate them to the platforms/DPIs you ship.

---

## 10. CLI reference

```
reaproof setup [--reaper VERSION] [--with-validators]   Download/locate pinned REAPER + validators
reaproof doctor [--signing]                             Verify the environment is correctly provisioned
reaproof init PATH                                      Scaffold a tests folder + reaproof.toml
reaproof new-test --type TYPE [--control C] [--param P] Generate a test (see §4 for types)
reaproof run [PATHS] [options]                          Run tests
    --mutation-check        Prove every assertion can fail (flag VACUOUS ones)
    --dpi 100,150,200       DPI scales to test
    --formats VST3,CLAP     Override formats
    --sample-rates 44100,48000,96000
    --block-sizes 64,512,1024
    --strictness N          Validator strictness (1–10)
    --skip-gui-tests        DSP-only (faster CI legs)
    --repeat N              Determinism: run each test N times (default 2)
    --seed 0xHEX            Fix RNG seed (recorded in provenance)
    --os linux|windows|macos
reaproof report [--open] [--format html|junit|json]     Show/export results + artifacts
reaproof goldens review | approve                       Review/approve changed reference images
reaproof ci init --provider github                      Generate CI workflow
```

---

### Where to go next
- The complete control-coverage taxonomy and tool/version details: [`REFERENCE.md`](REFERENCE.md).
- The universal, zero-code flow: [`../README.md`](../README.md) (`reaproof test <plugin>`).
