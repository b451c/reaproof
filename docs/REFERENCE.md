# ReaProof — Technical Reference

Lookup tables and grounding facts, verified against the state of the ecosystem in **June 2026**. Every claim here is sourced at the bottom ([§10](#10-sources)). Pin to these versions/flags; re-verify against the sources when bumping.

---

## 1. Pinned components (June 2026)

| Component | Pinned/Notes |
|---|---|
| **REAPER** | **7.75** (released 2026‑06‑23). The v7 line ships free updates ~every few weeks; pin an exact build and cache it. Runs natively on Windows (x64, arm64ec beta), macOS (Intel/Universal/Apple Silicon), Linux (x86_64, i686, armv7l, aarch64). Portable install + isolated resource dir supported. |
| **ReaScript** | Built‑in **Lua** and **EEL2** (no external deps); **Python** optional (install separately, enable in Prefs → Plug‑ins → ReaScript; some UI features unavailable from Python). C/C++ extension SDK also available. |
| **reapy / reapy‑boost** | Python wrappers over ReaScript via a `defer`/socket bridge. External calls capped at ≈30–60/s — batch with `inside_reaper`. `python-reapy` on PyPI; `reapy-boost` (Levitanus) adds JS_API/ReaImGui bindings. |
| **js_ReaScriptAPI** | juliansader. Window/pixel/input + LICE drawing + PNG write. Ships in the ReaTeam ReaPack repo (default). Cross‑platform; macOS/Linux builds marked experimental. |
| **SWS** | Companion extension; extra ReaScript functions (available via reapy too). |
| **ReaImGui** | cfillion; Dear ImGui binding for script UIs. |
| **pluginval** | Tracktion. VST2/VST3/AU; Win/macOS/Linux; headless; runs validation **out‑of‑process**. Strictness 1–10 (≥5 recommended for hosts). |
| **clap-validator** | free‑audio. CLAP; runs tests **out‑of‑process** (crash‑isolated), parallel by default; includes a fuzzing test. |
| **clap-info** | free‑audio/surge‑synthesizer. CLAP descriptor/port/param dump in **JSON**; scanner (`-l`, `-s`). |
| **auval** | Apple, macOS only; AudioUnit validation. |
| **Steinberg EditorHost** | VST3 SDK sample host; open a VST3 editor without a DAW. |
| **CLAP** | Standard at **1.2.6** (Oct 2025); REAPER supports CLAP. 1.2.6 added the `mini-curve-display` draft extension (plugins can hand the host small curves, e.g. EQ responses — relevant for visual checks). |
| **Xvfb** | X.Org virtual framebuffer; `xvfb-run`; multiple `-screen` for multi‑monitor; default 1280×1024×24. Use Xephyr for nested displays. |
| **Image diff** | pixelmatch and/or odiff (perceptual, AA‑aware). |
| **Audio** | NumPy, SciPy, soundfile, pyloudnorm (ITU‑R BS.1770 LUFS). |
| **Runner** | pytest (JUnit XML output for CI). |

---

## 2. REAPER command-line flags (the ones the harness uses)

REAPER CLI form: `reaper [options] [project.rpp | media.wav | script.lua ...]`

| Flag | Effect |
|---|---|
| `-renderproject file.rpp` | Render the project and exit. **Output filename/format/range come from the `.rpp`** (see [§5](#5-rpp-render-keys)), not the command line. Core of offline audio testing. |
| `-nosplash` | Suppress the splash window. |
| `-splashlog /path/log.txt` | Write the splash/startup message log to a file (first line includes the version) — useful diagnostics. |
| `-ignoreerrors` | Don't show error dialogs on load (won't block headless runs). |
| `-cfgfile file.ini` | Use a specific resource/config directory — **the isolation mechanism** for hermetic, reproducible config. |
| `-new` / `-template file.rpp` / `-saveas new.rpp` | Start new / from template / save‑as. |
| `-newinst` / `-nonewinst` | Force new instance / reuse a running instance (`-nonewinst media.wav script.lua` runs a script in a running instance). |
| `-noactivate` | Launch without activating the window (Win/macOS/Linux since build 7.29). |
| `-close[all][:save|:nosave][:exit]` | Close project(s); `:exit` since 7.29. |
| `-batchconvert filelist.txt` | Batch file conversion mode (tab‑separated in→out; supports NORMALIZE/BRICKWALL/FADE directives). |
| (positional) `project.rpp script.lua` | Since build 6.80, pass a project *and* a script together — e.g. launch with an isolated project and a bootstrap script that starts the bridge. |

> Unrecognised flags (`reaper -?`) print the CLI help, including the `-batchconvert` file‑list format.

**Typical harness invocations**

```bash
# Offline audio: render a templated project (settings baked into the .rpp)
reaper -nosplash -ignoreerrors -splashlog run.log -renderproject test.rpp

# Interactive/stateful session with isolated config + bootstrap bridge script
reaper -cfgfile profiles/ci/reaper.ini -nosplash project.rpp bridge/reaproof_bridge.lua

# Linux headless wrapper
xvfb-run -s "-screen 0 1920x1080x24" reaper -cfgfile ... project.rpp bridge.lua
```

---

## 3. Key APIs used (control / observation / visual / input)

### 3.1 ReaScript (`reaper.*`) — control & state
Representative functions (full list at the ReaScript API docs):
- Actions: `Main_OnCommand(cmd, flag)`, `NamedCommandLookup(name)`.
- FX params: `TrackFX_GetNumParams`, `TrackFX_GetParamNormalized` / `TrackFX_SetParamNormalized`, `TrackFX_GetParam` (with min/max), `TrackFX_GetFormattedParamValue`, `TrackFX_GetParamName`.
- FX/track/item/envelope model + project info: `TrackFX_AddByName`, `GetSetProjectInfo` / `GetSetProjectInfo_String` (e.g. `RENDER_*`, `RULER_LANE_DEFAULT:X`), `AddRegionOrMarker` (7.72+, returns a `ProjectMarker*`).
- State/chunks: `GetSetObjectState` / FX chunk get/set for preset round‑trips.
- Deferred loop: `defer` / `runloop` (the bridge's heartbeat + cooperative yielding); `atexit` for cleanup.

### 3.2 js_ReaScriptAPI — windows, pixels, input
- **Windows:** `JS_Window_Find`, `JS_Window_FindChild`, `JS_Window_ArrayAllChild`, `JS_Window_GetClientRect`, `JS_Window_GetRect`, `JS_Window_SetPosition` (move to another monitor), `JS_Window_GetTitle`.
- **Pixel capture:** `JS_LICE_CreateBitmap`, blit window contents into a LICE bitmap, `JS_LICE_WritePNG` (write the bitmap to a PNG) — DPI‑aware, window‑targeted screenshots. (On Windows, `JS_GDI_*` blits are available; for cross‑platform prefer the LICE path.)
- **Mouse:** `JS_Mouse_SetPosition`, `JS_Mouse_GetState`, `JS_Mouse_GetCursor`.
- **Keyboard:** `JS_VKeys_GetDown` / `GetState` / `GetUp`, `JS_VKeys_Intercept`.
- **Window messages (precise synthetic input relative to a window):** `JS_WindowMessage_Send`, `JS_WindowMessage_Post`, `JS_WindowMessage_Intercept` / `InterceptList` / `Release`.
- **Misc:** `JS_ReaScriptAPI_Version`, `JS_Window_GetPixel` (sample a pixel), list‑view/file helpers.

> Check exact, current signatures in the searchable ReaScript API doc (X‑Raym mirror) — js_ functions are listed there alongside native ones.

### 3.3 reapy (Python) — convenience layer
`reapy.Project()`, object‑oriented tracks/items/FX; `reapy.reascript_api.*` exposes all RPR_* functions (and SWS/JS_API where present); use `with reapy.inside_reaper():` to batch and beat the external call ceiling. Enable distant API via `reapy.configure_reaper()` (writes config + starts a server on a port).

---

## 4. Validator command reference

### 4.1 pluginval (VST2 / VST3 / AU)
```bash
# Linux/Windows
pluginval --strictness-level 5 --validate "/path/to/Plugin.vst3"
# macOS
/Applications/pluginval.app/Contents/MacOS/pluginval --strictness-level 10 \
    --validate-in-process "/path/to/Plugin.vst3"
```
Useful options: `--strictness-level 1..10` · `--skip-gui-tests` · `--timeout-ms N` (`-1` disables) · `--repeat N` · `--randomise` · `--seed 0xHEX` (reproduce a failure) · `--sample-rates 44100,48000,96000,192000` · `--block-sizes 64,128,256,512,1024` · `--output-dir DIR` · `--disabled-tests file`. Exit code **0** = pass, **1** = fail. At level ≥5 it also runs the Steinberg VST3 validator and (macOS) auval. **Gotchas:** no trailing slash on the plugin path; some multi‑bus VST3 instruments can crash the VST3 leg — fall back to the Steinberg validator.

Covers: cold/warm open timing, plugin info, automatable parameters, bus layouts, **parameter fuzz**, **parameter thread‑safety** (concurrent `setValue`), **allocation‑on‑audio‑thread** interceptors, **state‑restoration checksums**.

### 4.2 clap-validator (CLAP)
```bash
clap-validator validate /path/to/Plugin.clap
clap-validator validate /path/to/Plugin.clap --only-failed
clap-validator validate --in-process --test-filter <test-name> /path/to/Plugin.clap
clap-validator list tests        # enumerate tests (+ descriptions)
```
Runs tests **out‑of‑process** by default (crashes → results), **parallel** by default (`--no-parallel` to serialise). Includes a **fuzzing** test (random parameter permutations → process buffers → fail on infinite/NaN values or crash), state, threading, symbol‑resolution, and preset‑discovery tests.

### 4.3 clap-info (CLAP, machine‑readable)
```bash
clap-info -l                     # list installed CLAPs
clap-info -s                     # scan: print descriptors (acts as a scanner)
clap-info /path/to/Plugin.clap   # full JSON: ports, parameters, extensions
```
Use the JSON to **derive the parameter list** your coverage report checks against.

### 4.4 auval (macOS / AU)
```bash
auval -v aufx Gain MyCo          # validate by type/subtype/manufacturer
auval -a                         # list all AUs
```

### 4.5 Steinberg EditorHost (VST3 UI without a DAW)
Open the plugin's VST3 editor in the SDK's sample host for a UI smoke‑test or as a capture fallback when REAPER‑hosted capture is impractical.

---

## 5. `.rpp` render keys (bake render settings into the project)

`-renderproject` reads output settings from the project file. Template these in `rpp_templates/`:

```
RENDER_FILE "C:\path\out.wav"      # output path (absolute)
RENDER_PATTERN ""                  # wildcard filename pattern (overrides RENDER_FILE if set)
RENDER_FMT 0 2 0                   # format / channels / samplerate-mode (e.g. WAV)
RENDER_RANGE 1 0 0 18 1000         # bounds/source (entire project, time selection, etc.)
RENDER_SRATE <hz>                  # render sample rate (0 = project)
RENDER_1X 0                        # 1=realtime render
RENDER_RESAMPLE 0 0 1              # resample mode
RENDER_DITHER 0                    # dither/noise-shaping bits
RENDER_ADDTOPROJ 0                 # don't add result back to project
RENDER_STEMS 0                     # stem mode
RENDER_NORMALIZE ...               # normalization (peak/true-peak/LUFS-I/S/M), if used
```

The harness writes a temp copy of the template with these filled in, renders it, analyses the output, then discards the temp project (keeps the source template clean). Note: REAPER also offers **render statistics / loudness reports** (HTML, incl. dry‑run reports with no media written) which can supplement analysis.

---

## 6. Per-OS tooling table

| Concern | Windows | macOS | Linux |
|---|---|---|---|
| GUI/display | Interactive desktop session (CI: desktop runner/VM) | WindowServer / logged‑in session | **Xvfb** (`xvfb-run`); Xephyr for nesting; force `XDG_SESSION_TYPE=x11` on Wayland |
| Screenshot (fallback to in‑process capture) | GDI / DXGI | `screencapture`, CGWindowList | `xwd` / `scrot` / `ffmpeg` on the Xvfb display |
| OS‑level input (fallback to JS_WindowMessage) | `SendInput` (AutoHotkey, pydirectinput) | CGEvent, `cliclick` | `xdotool` (X11), `ydotool` (Wayland/uinput) |
| DPI scaling to test | per‑monitor DPI v2; 100/150/200% | Retina 2× | scale env / Xft.dpi |
| Audio (no hardware) | Dummy Audio device | Dummy Audio device | Dummy Audio / PipeWire or JACK null sink |
| Plugin signing | — | **codesign / notarization / Gatekeeper** must be satisfied or plugins won't load (verify or strip quarantine for dev builds) | — |
| Plugin dirs | `…\Common Files\VST3`,`\CLAP`,`\LV2`; VST2 custom | `~/Library/Audio/Plug-Ins/{VST3,Components,CLAP,LV2,VST}` | `~/.vst3`,`~/.clap`,`~/.lv2`,`~/.vst` |
| Arch | x64, arm64ec | Intel + Apple Silicon (test both / Universal) | x86_64, aarch64 |

**Multi‑monitor simulation:** Linux — multiple `-screen` entries under Xvfb (or Xephyr); Windows/macOS — configure multiple displays on the runner/VM or a virtual display driver. Test: placement per display, cross‑display drag, per‑monitor DPI correctness, value→visual after move, child/modal placement, position persistence on reopen.

---

## 7. Control-coverage taxonomy (completeness matrix)

A test suite is "complete" for a control when every applicable cell is exercised **and mutation‑verified**. Rows = control types; columns = dimensions. `●` = applies, `–` = N/A.

| Control \ Dimension | Func (range/taper/text/step/default) | DSP effect | Automation (incl. sample‑accurate) | State save/restore | Visual @ value (dual‑channel) | Redraw on change | Interaction (drag/wheel/dbl‑click/right‑click/fine) | DPI 100/150/200/Retina | Multi‑monitor | Theme/skin | Resize | Thread‑safety | Crash/fuzz |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Rotary knob | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| Slider / fader | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| Button / toggle | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| Multi‑state switch | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| Dropdown / combo | ● | ● | – | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| Numeric / text field | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| XY pad | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| Envelope / EQ curve | ● | ● | ● | ● | ● (nodes→curve→spectrum) | ● | ● | ● | ● | ● | ● | ● | ● |
| Meter | – | ● | – | – | ● (drawn level = measured) | ● | – | ● | ● | ● | ● | ● | ● |
| Waveform / spectrum | – | ● | – | – | ● (drawn = known signal) | ● | – | ● | ● | ● | ● | ● | ● |
| Keyboard (synth) | ● | ● | ● | – | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| Custom canvas | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |

The coverage report compares this matrix against the control set the plugin actually exposes (derived from `clap-info` / `TrackFX_GetNumParams` / the developer manifest) and lists uncovered cells.

---

## 8. The "no false results" guarantees (reference summary)

| Guarantee | Mechanism |
|---|---|
| Pass ⇒ feature works | Assert on independent observation of the *effect* (audio/pixels/state), never on the set value. |
| No vacuous green | **Mutation check** proves each assertion can fail; VACUOUS ones fail the suite. |
| No flaky pass/fail | Determinism lock + run ≥2× + identical‑within‑tolerance; disagreement ⇒ quarantine, never retry‑to‑green. |
| No timing artifacts | `wait_until(predicate, timeout)`; `sleep()` banned in assertions. |
| Tight but fair tolerances | Bit‑exact/exact‑match default; perceptual thresholds calibrated by the mutation check. |
| Visual = precise *and* meaningful | Dual‑channel cross‑check (reported value vs drawn value). |
| Crashes/NaN counted | Out‑of‑process validators + supervisor + pathology detection ⇒ FAIL, never skip. |
| Reproducible | Provenance manifest + artifacts on every result. |

---

## 9. Glossary

- **Dual‑channel cross‑check** — verifying a value‑bearing control by both its reported value and the value implied by drawn pixels, requiring agreement.
- **Mutation check** — deliberately perturbing an assertion's target to confirm the assertion turns red (proving it's non‑vacuous).
- **Golden image** — an approved reference screenshot, keyed by OS/DPI/theme/plugin/version/value.
- **Null test** — subtracting output from a reference to measure residual difference (ideally bit‑exact).
- **Determinism lock** — the set of pinned inputs (REAPER build, config, theme, fonts, SR, block size, DPI, seed, locale, rendering mode) that make a run reproducible.
- **PDC** — plugin delay compensation; the latency a plugin reports and the host compensates for.

---

## 10. Sources

Verified June 2026. Re‑check before bumping pinned versions.

- REAPER versions/changelog & downloads: `reaper.fm/download.php`, `reaper.fm/download-old.php`, `reaper.fm/whatsnew.txt`, `reaper.blog`.
- REAPER CLI flags: ReaTeam/Doc `REAPER-CLI.md` (github.com/ReaTeam/Doc), docEdub/Reaper‑Docs mirror; `-renderproject` behaviour & render keys: Cockos forum archive threads.
- ReaScript API (Lua/EEL2/Python, C/C++ SDK): `reaper.fm/sdk/reascript/reascripthelp.html`; searchable mirror: extremraym.com ReaScript doc.
- reapy / reapy‑boost: github.com/RomeoDespres/reapy, python‑reapy.readthedocs.io, github.com/Levitanus/reapy‑boost, PyPI `python-reapy`.
- js_ReaScriptAPI: github.com/juliansader/js_ReaScriptAPI (source lists `JS_Window_*`, `JS_Mouse_*`, `JS_VKeys_*`, `JS_WindowMessage_*`, `JS_LICE_*`, `JS_LICE_WritePNG`); ReaLinks overview.
- pluginval: github.com/Tracktion/pluginval, tracktion.com/develop/pluginval; options/strictness & headless usage from the README and the iPlug3 validator skill notes.
- clap-validator / clap-info / CLAP: github.com/free-audio/clap-validator (+ releases), github.com/free-audio/clap-info, github.com/free-audio/clap, cleveraudio.org; CLAP 1.2.6 / mini‑curve‑display per CLAP project notes.
- Xvfb: pypi.org/project/xvfbwrapper, X.Org Xvfb man page; headless‑GUI‑+‑screenshot pattern (general CI practice).
- Existing agent/automation prior art: iPlug3 `audio-plugin-dev-skills` (validate‑pluginval / validate‑vst3 / validate‑clap / editorhost / auval / codesign skills); reapy‑based REAPER MCP servers (e.g. shiehn/total‑reaper‑mcp, wegitor/reaper‑reapy‑mcp).

> Anthropic/Claude Code feature details (subagents, hooks, skills, MCP) change frequently — confirm against the Claude Code docs map rather than this file.
