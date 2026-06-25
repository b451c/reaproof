# Examples

Reference subjects and a worked custom test. These double as the platform's own fixtures:
every gate is demonstrated with a passing case **and** a failing negative control, so the
checks are proven non-vacuous.

## Reference subjects

| Path | What it is |
|---|---|
| `jsfx/ReaProof_Gain.jsfx` | A JSFX gain plugin with a custom `@gfx` rotary knob (the dual-channel visual subject). |
| `jsfx/ReaProof_Gain_BrokenIgnore.jsfx` | Same UI, but it **ignores** its parameter - the negative control for "the effect tracks the value". |
| `jsfx/ReaProof_Gain_BrokenNaN.jsfx` | Emits NaN - the negative control for pathology detection. |
| `jsfx/ReaProof_Gain_BrokenAngle.jsfx` | Draws the knob at the **wrong angle** - the negative control for the dual-channel pixel cross-check. |
| `jsfx/ReaProof_Mute_Button.jsfx` | A toggle control (a second control type). |
| `clap/reaproof_gain.c` + `build_clap.sh` | A minimal CLAP gain plugin (build it into a `.clap` bundle to try `reaproof test`). |

## Worked custom test

`generated/test_gainknob.py` is a short, semantic spec: it asserts the gain plugin's
*rendered* output matches the dB it was set to, and that the knob's drawn angle agrees with
its reported value - each assertion mutation-verified. Use it as a template for your own
"does it do the right thing" tests. Run a folder of such specs with:

```bash
reaproof run examples/generated -p reaproof.runner.pytest_plugin
```

Remember: you only need these when you want to check *semantics*. For "does it load, is it
stable, deterministic, pathology-free across all settings", just run `reaproof test <plugin>`.
