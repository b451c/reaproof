"""Gates: JSFX zero-code battery (forum ask, catalog credit: mequaz t=310135 #5).

The broken reference subjects ARE the mutations: each battery stage is proven
able to turn RED by a subject engineered to trip exactly that stage, and the
clean subject proves the green path (twice, §1.4). The battery itself is built
on live-verified 7.75 mechanics — see the module docstring of
``reaproof.runner.jsfxtest`` (a compile-broken JSFX still inserts, still lists
its sliders as params, and passes audio bit-exact; only the FX window carries
the compiler message).
"""
import sys
from pathlib import Path

import pytest

from reaproof import paths
from reaproof.runner.jsfxtest import (
    _ERROR_TEXT, JsfxTestOptions, collect_dependencies, parse_jsfx,
    run_jsfx_battery,
)

# REAPER-launching gates run on macOS and Linux (both session stacks are
# live-verified); the parser units and static-only battery paths run
# everywhere. Windows is excluded from THIS suite only because its battery
# leg runs against a provisioned VM, not the local host, for now.
darwin = pytest.mark.skipif(sys.platform == "win32",
                            reason="Windows battery leg runs on the "
                                   "provisioned VM, not in this suite yet")

JSFX = paths.EXAMPLES / "jsfx"


def _statuses(rs) -> dict[str, str]:
    return {r.name: r.status for r in rs.results}


def _quiet(*a, **k):
    pass


# ---- REAPER-free units: the static parser ------------------------------------

def test_parser_reads_the_gain_subject():
    src = parse_jsfx((JSFX / "ReaProof_Gain.jsfx").read_text())
    assert src.desc == "ReaProof Gain (custom rotary knob)"
    assert [s.name for s in src.sliders] == ["Gain (dB)"]
    s = src.sliders[0]
    assert (s.number, s.default, s.lo, s.hi, s.hidden) == (1, 0.0, -24.0, 24.0, False)
    assert src.sections == ["init", "slider", "sample", "gfx"]
    assert src.writes_audio and not src.uses_midi
    assert src.gfx_functions == ["draw_knob"]
    # draw_knob is only used inside @gfx — legal, must NOT be flagged
    assert src.gfx_funcs_used_outside == []


def test_parser_sparse_and_hidden_sliders_in_number_order():
    src = parse_jsfx((JSFX / "ReaProof_Sparse_Hidden.jsfx").read_text())
    assert [(s.number, s.name, s.hidden) for s in src.sliders] == [
        (1, "Gain (dB)", False), (5, "Mix", False), (40, "Drive scale", True)]


def test_parser_flags_gfx_function_used_outside_gfx():
    src = parse_jsfx((JSFX / "ReaProof_Gain_BrokenCompile.jsfx").read_text())
    assert src.gfx_functions == ["db2gain"]
    assert src.gfx_funcs_used_outside == ["db2gain"]


def test_parser_variants_file_enum_gmem_nodesc_comments():
    text = (
        "options:gmem=AcmeSpace\n"
        "slider1:/samples:none:Sample file\n"
        "slider2:mode=1<0,3,1{A,B,C,D}>Mode\n"
        "slider3:0.5<0,1>Plain\n"
        "@sample\n"
        "// spl0 = 0/0; (commented out — must not count as an spl write)\n"
        "/* function fake() in a comment */\n"
        "x = 1;\n")
    src = parse_jsfx(text)
    assert src.desc is None
    assert src.gmem_namespace == "AcmeSpace"
    f, e, p = src.sliders
    assert f.is_file and f.name == "Sample file"
    assert (e.lo, e.hi) == (0.0, 3.0)
    assert (p.lo, p.hi) == (0.0, 1.0)
    assert not src.writes_audio and src.gfx_functions == []


def test_parser_tags_pins_shape_and_midi_variants():
    text = (
        "desc:Widest header\n"
        "tags: synthesis Instrument mono\n"
        "slider1:5<0,10,0.1:log>Log freq\n"
        "in_pin:none\n"
        "out_pin:main L\nout_pin:main R\nout_pin:aux L\nout_pin:aux R\n"
        "@block\nmidisyx(ofs, #buf);\n")
    src = parse_jsfx(text)
    assert src.tags == ["synthesis", "instrument", "mono"]
    assert src.is_instrument            # official v6.74+ signal, case-folded
    s = src.sliders[0]
    assert (s.lo, s.hi) == (0.0, 10.0)  # the :log shape rides the inc field
    assert src.in_pin_none and src.in_pins == 0
    assert src.out_pins == 4 and not src.out_pin_none
    assert src.uses_midi                # midisyx counts, not only recv/send


def test_parser_imports_and_filenames():
    src = parse_jsfx((JSFX / "ReaProof_Gain_Imports.jsfx").read_text())
    assert src.imports == ["lib/ReaProof_DSP_Lib.jsfx-inc"]
    text = "desc:x\nfilename:0,data/ir.wav\nfilename:1,skin.png\n@sample\nx=1;\n"
    assert parse_jsfx(text).filenames == ["data/ir.wav", "skin.png"]


def test_collect_dependencies_resolves_nested_and_guards_escape(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "fx.jsfx").write_text(
        "desc:t\nimport lib/a.jsfx-inc\nfilename:0,data.bin\n@sample\nx=1;\n")
    (tmp_path / "lib" / "a.jsfx-inc").write_text(
        "import b.jsfx-inc\nimport ../../escape.jsfx-inc\n@init\nfunction f() (1);\n")
    (tmp_path / "lib" / "b.jsfx-inc").write_text("@init\nfunction g() (2);\n")
    (tmp_path / "data.bin").write_bytes(b"\x00")
    deps, missing = collect_dependencies(tmp_path / "fx.jsfx")
    rels = sorted(r for _, r in deps)
    # nested import resolves RELATIVE TO THE IMPORTING FILE (lib/), and the
    # two-level ../.. escape is refused, not silently installed
    assert rels == ["data.bin", "lib/a.jsfx-inc", "lib/b.jsfx-inc"]
    assert missing == ["../../escape.jsfx-inc"]


def test_collect_dependencies_names_missing_imports():
    deps, missing = collect_dependencies(JSFX / "ReaProof_Gain_BrokenImport.jsfx")
    assert deps == []
    assert missing == ["lib/DoesNotExist.jsfx-inc"]


def test_missing_import_is_a_named_static_failure(tmp_path):
    """No REAPER needed: the battery stops at the static stage with the
    missing reference NAMED — an import that cannot install cannot compile."""
    rs = run_jsfx_battery(JSFX / "ReaProof_Gain_BrokenImport.jsfx",
                          out_dir=tmp_path, log=_quiet)
    assert not rs.gate_green
    static = next(r for r in rs.results if r.name.startswith("static:"))
    assert static.status == "failed"
    assert "DoesNotExist" in static.message
    assert len(rs.results) == 1         # nothing ran on top of the broken base


def test_reapack_header_parses_jsfx_double_slash_comments():
    from reaproof.runner.scripttest import parse_reapack_header
    jsfx = "desc:My effect\n// @version 1.2.3\n// @author someone\nslider1:0<0,1,0.1>x\n"
    hdr = parse_reapack_header(jsfx)
    assert hdr and hdr["version"] == "1.2.3" and hdr["author"] == "someone"


def test_error_regex_matches_real_compiler_messages():
    # both texts read verbatim from the FX window of broken subjects (7.75)
    assert _ERROR_TEXT.match("@sample:17: syntax error: missing ) or ]")
    assert _ERROR_TEXT.match("@sample:17: 'apply' undefined: 'spl0 =  <!> apply(spl0);'")
    # desc text / slider labels must NOT look like errors
    assert not _ERROR_TEXT.match("ReaProof Gain (custom rotary knob)")
    assert not _ERROR_TEXT.match("Gain (dB)")
    assert not _ERROR_TEXT.match("2 in 2 out")


# ---- battery gates (REAPER) ---------------------------------------------------

@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_clean_subject_is_green_twice(tmp_path):
    for i in (1, 2):   # §1.4: identical outcome across runs
        rs = run_jsfx_battery(JSFX / "ReaProof_Gain.jsfx",
                              out_dir=tmp_path / f"run{i}", log=_quiet)
        st = _statuses(rs)
        assert rs.gate_green, st
        assert st["compile: REAPER inserts and compiles the JSFX"] == "passed"
        assert st["params: declared sliders match live params"] == "passed"
        assert st["state: remove + re-add is factory reset"] == "passed"
        assert st["determinism: re-render is bit-identical"] == "passed"
        assert st["param sweep [0] Gain (dB): stable across range"] == "passed"


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_compile_broken_is_red_with_the_compiler_error(tmp_path):
    """Catalog item 1: the file inserts silently; the battery must still turn
    RED and carry REAPER's own compiler message + the @gfx-function hint."""
    rs = run_jsfx_battery(JSFX / "ReaProof_Gain_BrokenCompile.jsfx",
                          out_dir=tmp_path, log=_quiet)
    assert not rs.gate_green
    comp = next(r for r in rs.results
                if r.name == "compile: REAPER inserts and compiles the JSFX")
    assert comp.status == "failed"
    assert "db2gain" in comp.message and "undefined" in comp.message
    assert "@gfx" in comp.message            # the likely-cause hint names item 1
    # the battery STOPS: no audio render may green-wash a non-compiling subject
    assert not any(r.name.startswith("audio:") for r in rs.results)


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_sparse_hidden_mapping_and_hidden_sweep(tmp_path):
    """Catalog item 6: sparse slider numbers resolve as dense params by name,
    and the hidden slider is swept like any other param."""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Sparse_Hidden.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signals=False), log=_quiet)
    st = _statuses(rs)
    assert rs.gate_green, st
    par = next(r for r in rs.results
               if r.name == "params: declared sliders match live params")
    assert "3 slider(s) (1 hidden)" in par.message
    assert st["param sweep [2] Drive scale (hidden): stable across range"] == "passed"


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_runaway_loop_at_extreme_is_red_in_the_sweep(tmp_path):
    """The honest extreme-setting failure for JSFX: the host scrubs non-finite
    output and clamps to ±1 (a JSFX cannot emit NaN into the graph), so a
    runaway @sample loop that STALLS the render is what a sweep-reachable
    extreme really does in the wild — and it must fail via the render
    timeout, not wedge the battery. This also proves the staircase envelope
    actually REACHES the top of the declared range (plain-unit scale)."""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Gain_BrokenHang.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signals=False, render_timeout=45.0), log=_quiet)
    assert not rs.gate_green
    sweep = next(r for r in rs.results if r.name.startswith("param sweep [0]"))
    assert sweep.status == "failed"
    # either the render never produced output (timeout) or it left a stable
    # partial file (truncation guard) — both are the stall, named
    assert "no output within" in sweep.message or "truncated" in sweep.message, \
        sweep.message


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_import_subject_compiles_with_its_library(tmp_path):
    """A JSFX shipped with a library must pass the compile proof — the battery
    installs import references alongside, preserving the relative layout.
    (Without dependency installation this exact subject fails compile with
    'db2gain' undefined — the BrokenCompile fixture proves that red path.)"""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Gain_Imports.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signals=False, sweep_params=False), log=_quiet)
    st = _statuses(rs)
    assert rs.gate_green, st
    assert st["compile: REAPER inserts and compiles the JSFX"] == "passed"
    static = next(r for r in rs.results if r.name.startswith("static:"))
    assert "installs 1 referenced file" in static.message


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_quad_split_renders_four_channels():
    """Multichannel plumbing: a 4-out subject must reach channels 3/4 in the
    render (track/master/output sized from the declared out_pin count), and
    the duplicated pair must sit at exactly half amplitude."""
    from reaproof.observe.audio import analysis as A
    from reaproof.observe.audio import signals as S
    from reaproof.observe.audio.render import render_through_jsfx
    r = render_through_jsfx(
        "JS:ReaProof/ReaProof_Quad_Split.jsfx",
        jsfx_files=[JSFX / "ReaProof_Quad_Split.jsfx"],
        input_signal=S.noise(dbfs=-12.0, seconds=0.5, sr=48000),
        sample_rate=48000, channels=4, name="quadgate")
    assert r.samples.shape[1] == 4, r.samples.shape
    rms0 = A.rms_dbfs(r.samples[:, 0])
    rms2 = A.rms_dbfs(r.samples[:, 2])
    # a silent channel 3 would sit at -inf, not at -6 dB relative — the
    # relation itself is the proof the upper channels carried audio
    assert A.approx_dbfs(rms2, rms0 - 6.02, tol_db=0.2,
                         why="out 3 duplicates out 1 at exactly half amplitude"), \
        f"ch1={rms0:.2f} dBFS ch3={rms2:.2f} dBFS"


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_synth_sounds_and_is_deterministic_under_the_note_feed(tmp_path):
    """An instrument subject is driven by the deterministic reference note
    clip: it must produce audio, stay pathology-free and re-render
    bit-identically. tags: instrument is detected from the source."""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Synth.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signal_names=("sine_1k",), extra_rates=(),
                             sweep_params=True), log=_quiet)
    st = _statuses(rs)
    assert rs.gate_green, st
    assert st["midi: renders under the reference note feed"] == "passed"
    prod = next(r for r in rs.results
                if r.name == "midi: produces audio for the note feed")
    assert prod.status == "passed" and "dBFS" in prod.message
    assert st["midi: note-feed re-render is bit-identical"] == "passed"
    # no false red from the input-signal determinism check (synth is silent
    # without notes — proven by the note-feed re-render instead)
    assert st["determinism: re-render is bit-identical"] == "skipped"
    assert st["param sweep [0] Level: stable across range"] == "passed"


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_silent_sampler_shape_skips_honestly(tmp_path):
    """A GUI-loading sampler keeps its material in @serialize, not in file
    sliders — silent-without-a-setup must be an HONEST SKIP with the capture
    instruction, never a false red (a pure synth with no serialized state
    still hard-fails: it has nothing to wait for)."""
    subj = tmp_path / "SamplerShape.jsfx"
    subj.write_text(
        (JSFX / "ReaProof_Synth_BrokenSilent.jsfx").read_text().replace(
            "@sample\n",
            "@serialize\nfile_var(0, loaded_setup);\n\n@sample\n", 1))
    rs = run_jsfx_battery(
        subj, out_dir=tmp_path / "out",
        opts=JsfxTestOptions(signal_names=("sine_1k",), extra_rates=(),
                             sweep_params=False), log=_quiet)
    prod = next(r for r in rs.results
                if r.name == "midi: produces audio for the note feed")
    assert prod.status == "skipped", prod.message
    assert "@serialize" in prod.message and "prevchunk" in prod.message


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_silent_synth_is_red_for_the_note_feed(tmp_path):
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Synth_BrokenSilent.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signal_names=("sine_1k",), extra_rates=(),
                             sweep_params=False), log=_quiet)
    assert not rs.gate_green
    prod = next(r for r in rs.results
                if r.name == "midi: produces audio for the note feed")
    assert prod.status == "failed"
    assert "silent" in prod.message


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_wellbehaved_serialize_is_green(tmp_path):
    """Catalog items 3-4, green path: a parameter-derived payload is byte-
    identical across repeated saves, and a mid-stream truncation restores to
    defaults with the FX intact."""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Ser_Good.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signals=False, sweep_params=False), log=_quiet)
    st = _statuses(rs)
    assert rs.gate_green, st
    assert st["state: repeated saves serialize identically"] == "passed"
    assert st["state: truncated serialize data restores to defaults"] == "passed"
    assert st["state: remove + re-add is factory reset"] == "passed"


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_prev_release_chunk_roundtrip(tmp_path):
    """Catalog item 5 (opt-in): the battery emits virgin_state.chunk; shipped
    next to the subject as <name>.prevchunk it must load cleanly into the
    current build. Proven with the battery's own artifact as the capsule."""
    import shutil
    subj = tmp_path / "Ser_Good.jsfx"
    shutil.copy2(JSFX / "ReaProof_Ser_Good.jsfx", subj)
    rs1 = run_jsfx_battery(subj, out_dir=tmp_path / "r1",
                           opts=JsfxTestOptions(signals=False,
                                                sweep_params=False), log=_quiet)
    st1 = _statuses(rs1)
    assert st1["state: previous-release chunk loads cleanly"] == "skipped"
    cap = tmp_path / "r1" / "virgin_state.chunk"
    assert cap.exists() and "<JS " in cap.read_text()
    shutil.copy2(cap, tmp_path / "Ser_Good.jsfx.prevchunk")
    rs2 = run_jsfx_battery(subj, out_dir=tmp_path / "r2",
                           opts=JsfxTestOptions(signals=False,
                                                sweep_params=False), log=_quiet)
    st2 = _statuses(rs2)
    assert st2["state: previous-release chunk loads cleanly"] == "passed", st2


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_unstable_serialize_payload_is_red(tmp_path):
    """Catalog item 3: a payload that differs between two back-to-back saves
    (the deterministic proxy for an @serialize/audio race) must turn RED."""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Ser_BrokenRace.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signals=False, sweep_params=False), log=_quiet)
    ser = next(r for r in rs.results
               if r.name == "state: repeated saves serialize identically")
    assert ser.status == "failed"
    assert "DIFFERENT payloads" in ser.message


@darwin
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_unguarded_truncation_restore_is_red(tmp_path):
    """Catalog item 4: a restore loop that trusts a count from the stream
    half-applies a truncated payload into a slider — the defaults check must
    catch the garbage."""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Ser_BrokenTrunc.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signals=False, sweep_params=False), log=_quiet)
    tr = next(r for r in rs.results
              if r.name == "state: truncated serialize data restores to defaults")
    assert tr.status == "failed", tr.message
    assert "file_avail" in tr.message or "wedged" in tr.message


@darwin
@pytest.mark.skipif(sys.platform == "linux",
                    reason="no host-visible leak channel on idle Linux "
                           "dummy-audio sessions: neither an @init slider "
                           "write nor @block slider_automate propagates to "
                           "the wrapper there (live-verified on 7.69 VM and "
                           "7.75 CI; macOS propagates both). The chunk "
                           "oracle itself is exercised cross-platform by the "
                           "serialize gates.")
@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.negative_control
def test_state_leak_survives_readd_is_red(tmp_path):
    """Catalog item 7: state leaking through gmem into a fresh instance means
    remove + re-add is NOT a factory reset — the chunk diff must turn RED."""
    rs = run_jsfx_battery(
        JSFX / "ReaProof_Gain_BrokenReset.jsfx", out_dir=tmp_path,
        opts=JsfxTestOptions(signals=False, sweep_params=False), log=_quiet)
    st = _statuses(rs)
    reset = next(r for r in rs.results
                 if r.name == "state: remove + re-add is factory reset")
    assert reset.status == "failed", st
    assert reset.mutation_verified        # the tweak WAS visible to the oracle
    assert "leak" in reset.message
