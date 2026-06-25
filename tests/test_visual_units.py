"""Unit tests for the visual algorithms on SYNTHETIC images (no REAPER, fast).

These verify the analysis machinery (angle measurement, tiered diff, vision, golden)
independently of capture, so the algorithms are covered even where screen capture is
unavailable (e.g. CI legs without a display).
"""
import math

import numpy as np
import pytest

from reaproof.observe.visual import diff as D
from reaproof.observe.visual import vision as V
from reaproof.observe.visual.golden import GoldenKey, GoldenStore
from reaproof.observe.visual.knob import angle_to_value, measure_knob_value


def render_knob(angle_deg: float, size=220, rgb=(235, 184, 51)) -> np.ndarray:
    """Draw a knob like Subject #1's @gfx: yellow indicator from centre at angle_deg
    (0=up, clockwise), with a tip dot. Pure numpy, for testing the measurement."""
    img = np.full((size, size, 3), 32, dtype=np.uint8)
    cx = cy = size / 2
    L = size * 0.38
    ang = math.radians(angle_deg)
    tx, ty = cx + math.sin(ang) * L, cy - math.cos(ang) * L
    for t in np.linspace(0, 1, 400):  # draw the line
        x, y = int(round(cx + (tx - cx) * t)), int(round(cy + (ty - cy) * t))
        img[max(0, y - 1):y + 1, max(0, x - 1):x + 1] = rgb
    yy, xx = np.ogrid[:size, :size]
    img[(xx - tx) ** 2 + (yy - ty) ** 2 <= 16] = rgb  # tip dot
    # a faint gray rim + light deterministic dither, so the synthetic frame has the
    # colour variety of a real render (and isn't flagged degenerate by the vision scan)
    ring = np.abs(np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) - L) < 2
    dark = img.sum(2) < 110
    img[ring & dark] = (100, 100, 110)
    rng = np.random.default_rng(0)
    img = np.clip(img.astype(np.int16) + rng.integers(-2, 3, img.shape), 0, 255).astype(np.uint8)
    return img


@pytest.mark.parametrize("set_db", [-24, -12, -6, 0, 6, 12, 24])
def test_knob_angle_measurement_on_synthetic(set_db):
    angle = (set_db + 24) / 48 * 270 - 135  # inverse of the @gfx mapping
    img = render_knob(angle)
    r = measure_knob_value(img, vmin=-24, vmax=24, chrome_top_frac=0.0)
    assert abs(r.value - set_db) < 1.0, f"{set_db}: measured {r.value:.2f}"


def test_angle_to_value_endpoints():
    assert angle_to_value(-135, -24, 24) == pytest.approx(-24)
    assert angle_to_value(135, -24, 24) == pytest.approx(24)
    assert angle_to_value(0, -24, 24) == pytest.approx(0)


def test_diff_exact_and_perceptual_and_1deg_sensitivity():
    a = render_knob(0.0)
    assert D.compare(a, a.copy()).exact                       # identical -> exact
    b = render_knob(0.0); b[0, 0] = (255, 0, 0)
    assert not D.exact_equal(a, b)
    # a 1° rotation must diff above a tight threshold (calibration target, §9.3)
    r = D.compare(render_knob(0.0), render_knob(1.0), threshold=16)
    assert r.changed_fraction > 0.0 and r.ssim < 1.0


def test_vision_flags_black_and_blank():
    assert V.glitch_scan(np.zeros((50, 50, 3), np.uint8))      # all black
    assert V.glitch_scan(np.full((50, 50, 3), 255, np.uint8))  # all white
    assert not V.glitch_scan(render_knob(30.0))                # a real frame is clean
    with pytest.raises(V.FrameGlitch):
        V.assert_no_glitch(np.zeros((50, 50, 3), np.uint8))


def test_golden_store_approve_compare_and_mismatch(tmp_path):
    store = GoldenStore(tmp_path)
    key = GoldenKey(plugin="P", version="1", control="Knob", state="0")
    img = render_knob(0.0)
    store.approve(img, key, approver="t", reason="r")
    assert store.compare(img, key).passed                      # same image matches
    assert store.meta_path(key).exists()                       # provenance recorded
    other = render_knob(45.0)
    assert not store.compare(other, key).passed                # a different render fails
