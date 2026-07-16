# Agent Test Authoring Charter (U7)

> **The protocol an AI coding agent follows to test a REAPER subject FROM ITS
> SOURCE CODE with ReaProof.** Tool-agnostic: written for any agent that can
> read files and run shell commands (Claude Code, Codex, aider, ...). The
> agent-facing entry points are only files and the `reaproof` CLI — no
> tool-specific API. Claude Code users: `/reaproof-author <path> [auto]`.
> Codex-style agents discover this via `AGENTS.md` at the repo root.

## 0. Modes

- **`interactive`** (default): the agent STOPS at the checkpoints marked
  ⏸ below and asks the user (priorities, ambiguous intent, tolerances).
  For users who want control.
- **`auto`**: the agent never asks. At every ⏸ checkpoint it makes the
  CONSERVATIVE choice, records it in the manifest as `"intent": "assumed"`
  with an `"assumption"` sentence, and continues. The final report lists
  every assumption in its own section — the user reviews decisions AFTER,
  instead of being interrupted. For users who just want results.

Auto mode changes WHO decides, never the evidence bar: the §1 doctrine
(below) binds identically in both modes.

## 1. The doctrine binds you (non-negotiable)

Everything in `README.md ("no false results") and docs/REFERENCE.md §8` §1 applies to every test you write:

1. Assert on the observable EFFECT (rendered audio, captured pixels, state
   read back another way) — never on the value you set.
2. Never mock the subject; stub only the environment.
3. Every value-bearing assertion must be **mutation-verified** (prove it can
   turn RED — via `mutation.mutation_check` or a broken variant/negative
   control). A test you cannot make fail is a test you may not report.
4. Run every REAPER-launching test **≥2×**; disagreement = quarantine and
   investigate, never retry-to-green.
5. `wait_until(predicate)`, never `sleep()` in assertion paths.
6. Tolerances explicit + justified in a comment; prefer exact math derived
   from the source (that is the whole point of having the code).
7. If a feature is untestable, record `"status": "untestable"` with a precise
   reason — an honest skip. NEVER fabricate a pass. Faking green is the
   single worst outcome; the platform will catch you (mutation checks,
   `--mutation-check`, negative controls), so don't try.

## 2. The protocol

### Step 1 — Inventory the subject from source
Read the subject's code COMPLETELY. Produce the feature inventory: every
user-meaningful behaviour, not just what has UI. Sources of features:
- exposed parameters (sliders/ports/params) and their MATH (formulas, ranges,
  clamps, special values — copy the formula into the manifest evidence),
- registered actions/commands, API functions, hooks,
- UI: windows, controls drawn, interactions handled (drag/click/wheel/keys),
- state: what persists (ExtState, project chunks, files), what must survive
  a restart,
- I/O: files read/written, formats parsed,
- error handling the code PROMISES (e.g. "malformed input shows a message").

Write `reaproof_features.json` (scaffold: `reaproof author <subject>`). One
entry per feature. `"evidence"` cites file:line/symbol. This manifest is the
contract for everything that follows.

### Step 2 — Map each feature to oracles
For each feature pick the observation channel(s) that measure its EFFECT:

| Oracle | Machinery | Use for |
|---|---|---|
| audio | `render_through_jsfx` / `render_through_plugin` + `observe.audio.analysis` | DSP: exact-math gain/filter/dynamics oracles |
| state | bridge eval, different-path readback, `hygiene.snapshot/diff` | project model, ExtState, persistence (`session.restart()`) |
| visual | `capture_stable` + `diff` / `knob.measure_*` (dual-channel §1.9) | drawn value vs reported value, redraw, themes |
| interaction | `WindowGesture` / `bridge_click` / `dialog_command` / `type_text` (see REFERENCE §3.2b for the macOS delivery matrix) | drags, clicks, keyboard |
| validator | pluginval / clap-validator / lv2_validate | format conformance (free) |
| battery | `reaproof test <subject>` | the universal floor (run it FIRST, always) |

⏸ CHECKPOINT (interactive): show the user the inventory + proposed oracle per
feature + what is untestable and why. Ask: priorities? missing features?
expected behaviour where the code is ambiguous?
(auto: priority = param/DSP features first, then state, then UI; ambiguity →
assume the code IS the intent, mark `"intent": "assumed"`.)

### Step 3 — Handle the unreachable honestly
- Feature entry points that are `local`/private with a modal-dialog-only
  trigger: propose a **gated test seam** (D26) — a few lines in the SUBJECT
  gated by an ExtState flag. ⏸ Ask before modifying subject code
  (auto: do NOT modify the subject; mark the feature
  `"status": "untestable", "reason": "needs gated seam (D26) — subject edit
  requires owner consent"`).
- Native modal dialogs, appearance pinning (D27), AUv2 hermetic installs:
  untestable with the documented reason. The DialogMonitor names stray
  dialogs; it does not make them testable.
- Remember D29: a ReaScript runtime error kills REAPER's defer engine — run
  subject code via the bridge `pcall` (message captured) or as a real action
  under the watchdog, and never reuse a session after an error sighting.

### Step 4 — Write the specs
- Scaffold with `reaproof new-test` or copy the closest pattern:
  `tests/test_phase{1,3,4}_gate.py` (audio/visual/interaction),
  `tests/test_lv2.py` (exact-math DSP oracle), a real-extension assessment
  (real extension), `examples/scripts/` battery subjects.
- One test file per subject in a dedicated dir (or the subject repo's own
  tests dir if the user prefers — ⏸ ask; auto: a `reaproof_tests/` dir).
- Derive tolerances from source math; comment the derivation.
- Every test lists the feature ids it covers in a `COVERS = [...]` constant —
  the manifest report cross-checks these.

### Step 5 — Verify like the platform verifies itself
1. `reaproof test <subject>` — the zero-code battery must be green (or its
   skips understood) BEFORE trusting your specs.
2. Run your specs. For each: confirm the mutation/negative control turns RED
   (broken variant, perturbed expectation, or `mutation_check`).
3. Run everything twice; identical results.
4. `reaproof features-report reaproof_features.json --tests <dir>` must pass:
   it REFUSES a manifest that claims `covered` without test references —
   the anti-fabrication rule is mechanical, not honor-based.

### Step 6 — Report
Write `REPORT.md` next to the manifest: feature table (covered / spec-needed /
untestable+reason), the assumptions section (every `"intent": "assumed"`
entry), how to re-run, and the honest tail. The MaxPane REPORT.md
"Not automated (honest)" section is the model.

## 3. Manifest schema (reaproof_features.json)

```json
{
  "subject": {"path": "…", "type": "script|extension|plugin|jsfx|theme",
               "source": "…dir or repo…"},
  "mode": "auto|interactive",
  "features": [
    {
      "id": "gain_db",                       // stable slug, referenced by tests
      "title": "Gain (dB) parameter",
      "kind": "param|action|ui|state|io|dsp",
      "evidence": "src/gain.c:62 k=powf(10,db/20)",
      "oracles": ["audio"],
      "status": "covered|spec-needed|untestable",
      "reason": "…required when untestable…",
      "tests": ["reaproof_tests/test_gain.py::test_minus12_exact"],
      "intent": "declared|assumed",
      "assumption": "…required when assumed…"
    }
  ]
}
```

Validation rules (enforced by `reaproof features-report`):
- `covered` ⇒ non-empty `tests`; `untestable` ⇒ non-empty `reason`;
  `intent: assumed` ⇒ non-empty `assumption`; ids unique.
- With `--tests <dir>`: every referenced test node must exist, and a test
  file's `COVERS` list must be consistent with the manifest.

## 4. For agent-tool integrators

The protocol's only dependencies are: read/write files, run `reaproof …` and
`pytest`. Claude Code ships a thin command (`.claude/commands/reaproof-author.md`);
Codex-style agents get the pointer from `AGENTS.md`. Any other harness: give
the agent this file as the task brief with the subject path and mode.
