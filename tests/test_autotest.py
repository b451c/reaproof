"""Unit tests for the universal autotest engine's pure helpers (no REAPER needed).

The end-to-end battery is exercised against real plugins in the examples/ subjects;
here we lock down the format detection, scan-cache parsing (name discovery), and the
staircase-envelope Lua generation that the universal flow depends on.
"""
import pytest

from reaproof.runner import autotest as AT


def test_detect_format():
    from pathlib import Path
    assert AT.detect_format(Path("x/Foo.clap")) == "clap"
    assert AT.detect_format(Path("x/Foo.vst3")) == "vst3"
    assert AT.detect_format(Path("x/Foo.vst")) == "vst"
    with pytest.raises(ValueError):
        AT.detect_format(Path("x/reaper_foo.dylib"))


def test_parse_clap_cache_finds_the_named_bundle():
    cache = (
        "[Other.clap]\n_=00\ncom.other=0|Other Thing (Vendor)\n\n"
        "[Foo_RP.clap]\n_=DEADBEEF\ncom.acme.foo=0|Foo Reverb (Acme)\n"
    )
    got = AT._parse_clap_cache(cache, "Foo_RP.clap")
    assert got == [("Foo Reverb (Acme)", "com.acme.foo")]
    # a bundle with two plugins inside
    multi = "[Bar_RP.clap]\ncom.a=0|A (V)\ncom.b=0|B (V)\n"
    assert AT._parse_clap_cache(multi, "Bar_RP.clap") == [("A (V)", "com.a"), ("B (V)", "com.b")]


def test_parse_vst_cache_takes_display_after_last_comma():
    cache = (
        "AIR_Delay.vst3=8007,65860{ABC,AIR Delay Pro (AIR Music Technology)\n"
        "Foo_RP.vst3=8003,123{DEF,Foo Comp (Acme)\n"
    )
    assert AT._parse_vst_cache(cache, "Foo_RP.vst3") == [("Foo Comp (Acme)", "")]
    assert AT._parse_vst_cache(cache, "Missing.vst3") == []


def test_staircase_envelope_lua_spans_the_range():
    lua = AT._staircase_env_lua(idx=2, lo=-24.0, hi=24.0, steps=8, dur=1.0)
    assert "GetFXEnvelope(tr,fx,2,true)" in lua
    assert "-24.000000" in lua and "24.000000" in lua   # both ends present
    assert lua.count("InsertEnvelopePoint") == 16        # 2 points per step * 8 steps


def test_autotest_options_defaults():
    o = AT.AutotestOptions()
    assert o.sample_rate == 48000 and o.sweep_params and o.max_params == 16 and not o.full
