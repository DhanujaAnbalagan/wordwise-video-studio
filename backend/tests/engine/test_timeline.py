import pytest
from backend.app.engine.types import Segment, Interval
from backend.app.engine.timeline import layout, src_to_tl, tl_to_src, present_intervals

# Helper to convert seconds to ms
def s_to_ms(seconds: float) -> int:
    return int(seconds * 1000)

def assert_approx(a, b, tol=1e-6):
    assert abs(a - b) <= tol, f"{a} != {b} within {tol}"

# Sample segments for the main numerical example
example_segments = [
    Segment(srcStart=s_to_ms(0), srcEnd=s_to_ms(10), speed=1.0),
    Segment(srcStart=s_to_ms(20), srcEnd=s_to_ms(30), speed=2.0),
    Segment(srcStart=s_to_ms(40), srcEnd=s_to_ms(50), speed=0.5),
]

# ---------- Layout Tests ----------
def test_layout_single_segment():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    placed = layout(segs)
    assert len(placed) == 1
    p = placed[0]
    assert p.tlStart == 0
    assert p.tlEnd == 1000

def test_layout_multiple_consecutive_segments():
    segs = [
        Segment(srcStart=0, srcEnd=1000, speed=1.0),
        Segment(srcStart=1000, srcEnd=2000, speed=1.0),
    ]
    placed = layout(segs)
    assert placed[0].tlStart == 0
    assert placed[0].tlEnd == 1000
    assert placed[1].tlStart == 1000
    assert placed[1].tlEnd == 2000

def test_layout_reordered_segments():
    segs = [
        Segment(srcStart=20_000, srcEnd=30_000, speed=2.0),
        Segment(srcStart=0, srcEnd=10_000, speed=1.0),
    ]
    placed = layout(segs)
    # First segment (20-30) edited duration = (10)/2 = 5
    assert placed[0].tlStart == 0
    assert placed[0].tlEnd == 5_000
    # Second segment (0-10) edited duration = 10
    assert placed[1].tlStart == 5_000
    assert placed[1].tlEnd == 15_000

def test_layout_different_speeds():
    segs = [
        Segment(srcStart=0, srcEnd=1000, speed=2.0),   # 0.5x edited duration
        Segment(srcStart=2000, srcEnd=3000, speed=0.5), # 2x edited duration
    ]
    placed = layout(segs)
    assert placed[0].tlStart == 0
    assert placed[0].tlEnd == 500  # (1000)/2
    assert placed[1].tlStart == 500
    assert placed[1].tlEnd == 2500  # (1000)/0.5 = 2000 added to 500

def test_layout_empty():
    assert layout([]) == []

# ---------- Source-to-Timeline Mapping ----------
def test_src_to_tl_normal():
    placed = layout(example_segments)
    # source 5 sec -> timeline 5 sec
    assert src_to_tl(placed, s_to_ms(5)) == 5_000
    # source 25 sec -> timeline 12.5 sec (12500 ms)
    assert src_to_tl(placed, s_to_ms(25)) == 12_500
    # source 45 sec -> timeline 25 sec (25000 ms)
    assert src_to_tl(placed, s_to_ms(45)) == 25_000
    # source inside cut (15 sec) returns None
    assert src_to_tl(placed, s_to_ms(15)) is None

def test_src_to_tl_exact_boundaries():
    placed = layout(example_segments)
    # Exact start of a segment
    assert src_to_tl(placed, s_to_ms(20)) == 10_000  # after first segment (10s)
    # Exact end of a segment
    assert src_to_tl(placed, s_to_ms(30)) == 15_000
    # End of last segment
    assert src_to_tl(placed, s_to_ms(50)) == 35_000

# ---------- Timeline-to-Source Mapping ----------
def test_tl_to_src_normal():
    placed = layout(example_segments)
    # timeline 5 sec -> source 5 sec
    res = tl_to_src(placed, 5_000)
    assert res is not None
    assert res["src"] == 5_000
    # timeline 12.5 sec -> source 25 sec
    res = tl_to_src(placed, 12_500)
    assert res["src"] == 25_000
    # timeline 25 sec -> source 45 sec
    res = tl_to_src(placed, 25_000)
    assert res["src"] == 45_000

def test_tl_to_src_out_of_range():
    placed = layout(example_segments)
    # Beyond total timeline (35s) should map to last src end (50s)
    res = tl_to_src(placed, 40_000)
    assert res is not None
    assert res["src"] == 50_000
    # Negative timeline value clamps to 0
    res = tl_to_src(placed, -1_000)
    assert res is not None
    assert res["src"] == 0

# ---------- Present Intervals ----------
def test_present_intervals_consecutive():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0), Segment(srcStart=1000, srcEnd=2000, speed=1.0)]
    intervals = present_intervals(segs)
    assert intervals == [Interval(start=0, end=2000)]

def test_present_intervals_disjoint():
    segs = [Segment(srcStart=0, srcEnd=500, speed=1.0), Segment(srcStart=1000, srcEnd=1500, speed=1.0)]
    intervals = present_intervals(segs)
    assert intervals == [Interval(start=0, end=500), Interval(start=1000, end=1500)]

def test_present_intervals_overlapping():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0), Segment(srcStart=500, srcEnd=1500, speed=1.0)]
    intervals = present_intervals(segs)
    assert intervals == [Interval(start=0, end=1500)]

def test_present_intervals_reordered():
    segs = [Segment(srcStart=1000, srcEnd=2000, speed=1.0), Segment(srcStart=0, srcEnd=500, speed=1.0)]
    intervals = present_intervals(segs)
    # order does not matter, result should be merged correctly and sorted
    assert intervals == [Interval(start=0, end=500), Interval(start=1000, end=2000)]

def test_present_intervals_empty():
    assert present_intervals([]) == []

# ---------- Purity Tests ----------
def test_functions_do_not_mutate_input():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0), Segment(srcStart=2000, srcEnd=3000, speed=2.0)]
    segs_copy = [Segment(s.srcStart, s.srcEnd, s.speed) for s in segs]
    placed = layout(segs)
    _ = src_to_tl(placed, 500)
    _ = tl_to_src(placed, 500)
    _ = present_intervals(segs)
    assert segs == segs_copy, "Input segments were mutated"

def test_repeatability():
    segs = example_segments
    placed1 = layout(segs)
    placed2 = layout(segs)
    assert placed1 == placed2
    assert src_to_tl(placed1, s_to_ms(25)) == src_to_tl(placed2, s_to_ms(25))
    assert tl_to_src(placed1, 12_500) == tl_to_src(placed2, 12_500)
