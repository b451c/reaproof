# ReaProof

![platform: macOS](https://img.shields.io/badge/platform-macOS-informational)
![Windows / Linux: WIP](https://img.shields.io/badge/Windows%20%2F%20Linux-WIP-lightgrey)
![license: MIT](https://img.shields.io/badge/license-MIT-green)
![version](https://img.shields.io/badge/version-0.4.0-blue)

> **Platform: macOS today (full batteries).** Linux: the control plane and the
> offline render pipeline are live-verified against a real REAPER (structural
> JSFX battery runs green); the CI leg is being wired. Windows: the control
> plane (provisioner + bridge) is live-verified; renders and the full
> batteries are in progress.

**Trustworthy, automated testing for everything you build for REAPER — compiled
plugins (CLAP / VST / VST3 / LV2 / AU), JSFX, ReaScripts (Lua / EEL2 / Python),
native extensions, themes, and whole ReaPack repositories — by driving a real
REAPER and asserting on the observable effect.**

ReaProof loads your subject into a clean, isolated REAPER instance, drives it
the way a user would, renders real audio, captures real pixels from the real
window, reads the real project state, and asserts on what actually happened —
never on a mocked or simulated value. It is built around one rule:

> **A green result must mean it genuinely works. A red result must mean
> something genuinely broke. The tool must never produce a false result.**

Every check is *mutation-verified*: before a check is trusted, ReaProof proves
it can actually fail (by injecting a NaN, perturbing a value, or swapping in a
deliberately broken build). A check that cannot be made to fail is reported as
vacuous — never as green. Honest skips (with the precise reason) always beat
fake passes.

---

## Quickstart

```bash
pip install -e .
export REAPROOF_REAPER_APP=/Applications/REAPER.app   # point at your REAPER
reaproof doctor                                        # check the environment
```

Then test any subject with **one command and zero test code**:

```bash
reaproof test MyPlugin.clap            # compiled plugin battery
reaproof test MyPlugin.vst3
reaproof test MyPlugin.lv2
reaproof test MyEffect.jsfx            # JSFX battery (compile proof, slider
                                       # mapping, factory reset, multi-SR, sweep)
reaproof test my_script.lua            # ReaScript battery
reaproof test reaper_myext.dylib       # native-extension battery
reaproof test MyTheme.ReaperThemeZip   # theme battery
reaproof test MyPlugin.component       # AU battery (auval + load + render)
reaproof test my_script.py             # Python ReaScript battery
reaproof test-repo path/to/repo        # a whole ReaPack repository
open .cache/runs/autotest-*/report.html
```

## What each battery checks (no test code required)

| Subject | Battery |
|---|---|
| **CLAP / VST / VST3 / LV2** | format-validator conformance (clap-validator / pluginval / lv2_validate), loads in a real REAPER, every host-exposed parameter swept across its full range via automation envelopes (no NaN / Inf / denormal storms / crashes at any setting), deterministic re-render, silence-in → silence-out |
| **JSFX (.jsfx)** | compile proof surfacing REAPER's own compiler error text (a broken JSFX inserts silently), `import`/`filename:` references installed alongside, slider→param mapping (sparse + hidden), remove/re-add factory reset incl. serialized state, repeated-save serialize stability, truncated-restore-to-defaults, previous-release chunk compatibility (opt-in), multi-samplerate renders, full-range sweeps, deterministic MIDI note feed for instruments |
| **AU (.component)** | AudioComponents manifest, per-plugin `auval` conformance, semi-hermetic registration (AU discovery is system-wide — stated, cleaned up), loads in a real REAPER, render + bit-identical re-render |
| **ReaScript (.lua / .eel / .py)** | optional `luacheck`, ReaPack header lint (`--`/`//`/`#` comments), load-time errors captured with the real Lua message, deferred-phase runtime errors detected (even though REAPER halts the script engine on them), undeclared state leaks named (tracks, items, dirty flag, ExtState, windows, **gmem namespaces**), registers and runs as a real action (Python via the host-mirrored interpreter config), `--ui` window check |
| **Native extension (reaper_*.dylib)** | naming contract, load attempt proven from REAPER's own startup log, registration proven by an observable diff (actions + API functions) against a pristine instance, honest warning for hook-only extensions, opt-in `--run-actions` smoke under full supervision |
| **Theme (.ReaperThemeZip)** | structural lint (layout, every image decodes), live load with read-back, and a **paint proof** — main-window pixels must actually change |
| **ReaPack repository** | `reapack-index --check` metadata validation (when installed), package-layout rules enforced, per-package script and JSFX batteries with a logged budget cap |

Under every battery runs the **universal watchdog plane**: crash forensics
(macOS diagnostic reports collected as named evidence), a modal-dialog monitor
that names and dismisses stray dialogs even while REAPER's main thread is
blocked, a state-hygiene differ, and a full startup log per launch.

## Beyond zero-code: testing your subject's actual features

The batteries prove *stability*. For *semantic* correctness ("this knob is
exactly −6 dB", "this link moves the edit cursor") you write short specs on
ReaProof's oracles — audio measurements (RMS / LUFS / spectrum / null tests),
dual-channel visual checks (the drawn knob angle must agree with the reported
value), synthetic input, state read-back, persistence across restarts
(`session.restart()`).

**AI-assisted authoring** (`docs/AGENT_TEST_AUTHORING.md`): point an AI coding
agent (Claude Code, Codex, or any tool that reads files and runs a CLI) at
your subject's source and it follows a binding protocol — inventory every
feature from the code, map each to an oracle, write mutation-verified specs,
and report honestly what is covered, what is not, and why:

```bash
reaproof author path/to/subject --mode auto          # agent decides, records every assumption
reaproof author path/to/subject --mode interactive   # agent asks you at checkpoints
reaproof features-report reaproof_features.json --tests .   # anti-fabrication check
```

The manifest validator mechanically refuses "covered" claims that don't point
at real tests — an agent cannot mark features covered by prose.

## How the trust works

- **Assert on effects, never inputs** — rendered samples, captured pixels,
  state read back through a different path. The subject is never both actor
  and witness.
- **Mutation-verification** — every value-bearing assertion must prove it can
  turn red (`--mutation-check` enforces it suite-wide).
- **Determinism** — pinned REAPER build, isolated profile, locked locale/SR/
  block size/DPI; `--reaproof-repeat=N` re-runs every test and quarantines
  disagreement instead of retrying to green.
- **Negative controls** — every gate ships with a case that must fail (broken
  reference subjects are part of the repo).
- **Evidence** — every result carries artifacts (audio, screenshots, logs,
  diagnostic reports) and a provenance manifest whose REAPER build claim is
  checked against the app binary itself.

See `docs/REFERENCE.md` §8 for the full guarantee table.

## CLI overview

```
reaproof doctor                 environment health check
reaproof test <subject>         zero-code battery (plugin/AU/jsfx/script incl .py/extension/theme)
reaproof test-repo <dir>        ReaPack repository mode
reaproof author <subject>       scaffold AI-assisted test authoring
reaproof features-report <m>    validate a feature manifest (anti-fabrication)
reaproof run [paths]            run authored spec suites with the full
                                determinism lock, reports and provenance
reaproof new-test / init        scaffolding for hand-written specs
reaproof goldens                review/approve reference images (never auto)
```

## Repository layout

```
src/reaproof/        the platform: provision/ control/ observe/{audio,visual,input,
                     watchdog,hygiene} validators/ coverage/ report/ runner/
bridge/              the in-REAPER Lua control bridge
examples/            reference subjects incl. deliberately BROKEN variants
                     (jsfx, clap, lv2, native extensions, scripts)
tests/               the platform's own suite — it tests itself the same way
docs/                USER_GUIDE.md · REFERENCE.md · AGENT_TEST_AUTHORING.md
```

## Updating

ReaProof installs editable, so updating is one pull:

```bash
cd reaproof && git pull && reaproof doctor
```

`reaproof doctor` re-checks the environment after the update. Releases are
tagged (`git tag -l`); `main` is kept green (the platform tests itself with
its own doctrine). Heads-up: updates can ADD checks — a subject that was
green may honestly turn red because coverage grew, not because it regressed;
the report names exactly which new check fired.

## Requirements

- macOS (Apple Silicon tested), REAPER 7.x
- Python 3.10+ with `numpy scipy soundfile pyloudnorm pillow pytest`
- For visual/input planes: the js_ReaScriptAPI extension and Screen Recording
  permission for your terminal
- Optional: `pluginval`, `clap-validator`, `lv2` + `sord` (Homebrew),
  `luacheck`, `reapack-index` (Ruby ≥ 3.2) — every missing tool degrades to an
  honest, visible skip, never a silent pass

## Status

- **macOS**: fully supported — audio, validators, parameter sweeps, visual
  capture with dual-channel checks, input synthesis, watchdog, and all five
  subject-class batteries, exercised by the platform's own suite.
- **Linux**: platform-independent layers CI-tested; REAPER-driven planes
  implemented but not yet CI-verified.
- **Windows**: not yet.
- Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
