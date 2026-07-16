"""Gate: `eval` auto-wrap must not silently drop the value of a single-line
expression that contains an inner `return` (e.g. an inline IIFE).

The old heuristic (`"return" not in lua`) left `(function() ... return x end)()`
unwrapped, so its value was discarded and `eval` returned `nil` — a FALSE
result, the one outcome this platform must never produce. The unit cases are
mutation-proof: the IIFE case turns RED on the old heuristic. One REAPER e2e
confirms the observable effect end to end.
"""
import pytest

from reaproof.control.bridge_client import _wrap_lua


# ---- unit: pure, deterministic, no REAPER ---------------------------------

def test_bare_expression_is_wrapped():
    assert _wrap_lua("1 + 1") == "return (1 + 1)"
    assert _wrap_lua("reaper.CountTracks(0) == 2") == "return (reaper.CountTracks(0) == 2)"


def test_statement_chunks_are_left_alone():
    assert _wrap_lua("return 7") == "return 7"
    assert _wrap_lua("local x = 5; return x * 2") == "local x = 5; return x * 2"
    assert _wrap_lua("reaper.InsertTrackAtIndex(0,false)\nreturn true") \
        == "reaper.InsertTrackAtIndex(0,false)\nreturn true"
    # top-level `;` separator (non-keyword start) -> a chunk, must not wrap
    assert _wrap_lua("reaper.foo(); return true") == "reaper.foo(); return true"


def test_inline_iife_with_inner_return_is_wrapped():
    # The regression: these MUST be wrapped so their value is returned, not nil.
    # On the old `"return" not in lua` heuristic they stayed unwrapped (RED).
    assert _wrap_lua("(function() return 41 + 1 end)()") \
        == "return ((function() return 41 + 1 end)())"
    # inner `;` and `local` are nested (depth > 0) — still a single expression
    assert _wrap_lua("(function() local t = f(); return t + 5 end)()") \
        == "return ((function() local t = f(); return t + 5 end)())"


def test_nested_tokens_in_strings_and_calls_do_not_block_wrapping():
    # a string literal containing 'return'/';' must not be read as a statement
    assert _wrap_lua("reaper.JS_Window_Find('New Version', false) ~= nil") \
        == "return (reaper.JS_Window_Find('New Version', false) ~= nil)"
    assert _wrap_lua('x == "a; return b"') == 'return (x == "a; return b")'


def test_empty_chunk_is_untouched():
    assert _wrap_lua("   ") == "   "


# ---- e2e: real observable effect in REAPER --------------------------------

@pytest.mark.reaper
@pytest.mark.slow
@pytest.mark.gate
def test_iife_returns_its_value_in_reaper():
    from reaproof.runner.session import session
    with session("eval-wrap-gate") as s:
        # inner return + no leading keyword: exactly the trapped shape
        assert s.eval("(function() return 41 + 1 end)()") == 42
        # a nested-scope return inside a longer expression still comes back
        assert s.eval(
            "(function() local t = reaper.CountTracks(0); return t + 5 end)()"
        ) == 5
        # controls: a bare expression and an explicit-return chunk still work
        assert s.eval("6 * 7") == 42
        assert s.eval("return 'ok'") == "ok"
