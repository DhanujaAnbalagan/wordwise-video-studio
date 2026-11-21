"""
Tests for the low‑level timeline helper functions.

These tests cover the edge‑cases listed in the Phase 1 specification:
* split_at at start, middle, end, and outside a segment
* subtract_range with full, partial, and no overlap
* normalize_segments merging behaviour
* present_ms / timeline_duration with various speeds
* interval_minus correctness
* overlap calculation
* present_intervals merging & ordering
* place_word visibility in normal, partially cut, and fully cut scenarios
* input immutability for all helpers
"""

import copy
import math
from typing import List

import pytest

from backend.app.engine.types import Segment, Interval, Word, Ms
from backend.app.engine.timeline import (
    split_at,
    subtract_range,
    normalize_segments,
    present_ms,
    timeline_duration,
    interval_minus,
    overlap,
    present_intervals,
    place_word,
    place_word_slices,
    place_words_slices,
    layout,
)

# Helper to create a word spanning a given interval
def make_word(start: Ms, end: Ms, word_id: str = "w1") -> Word:
    return Word(id=word_id, index=0, text="test", start=start, end=end)

# -------------------- split_at --------------------

def test_split_at_beginning():
    segs = [Segment(srcStart=1000, srcEnd=2000, speed=1.0)]
    out = split_at(segs, 1000)  # split at segment start – no change
    assert out == segs

def test_split_at_middle():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    out = split_at(segs, 500)
    assert out == [
        Segment(srcStart=0, srcEnd=500, speed=1.0),
        Segment(srcStart=500, srcEnd=1000, speed=1.0),
    ]

def test_split_at_end():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    out = split_at(segs, 1000)  # split at end – no change
    assert out == segs

def test_split_at_outside():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    out = split_at(segs, 1500)  # point after segment – no change
    assert out == segs

# -------------------- subtract_range --------------------

def test_subtract_range_inside():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    r = Interval(start=200, end=800)
    out = subtract_range(segs, r)
    assert out == [
        Segment(srcStart=0, srcEnd=200, speed=1.0),
        Segment(srcStart=800, srcEnd=1000, speed=1.0),
    ]

def test_subtract_range_partial_left():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    r = Interval(start=-100, end=300)
    out = subtract_range(segs, r)
    assert out == [Segment(srcStart=300, srcEnd=1000, speed=1.0)]

def test_subtract_range_partial_right():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    r = Interval(start=700, end=1500)
    out = subtract_range(segs, r)
    assert out == [Segment(srcStart=0, srcEnd=700, speed=1.0)]

def test_subtract_range_fully_cover():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    r = Interval(start=-500, end=1500)
    out = subtract_range(segs, r)
    assert out == []

def test_subtract_range_no_overlap():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    r = Interval(start=1500, end=2000)
    out = subtract_range(segs, r)
    assert out == segs

# -------------------- normalize_segments --------------------

def test_normalize_adjacent_same_speed():
    segs = [
        Segment(srcStart=0, srcEnd=500, speed=1.0),
        Segment(srcStart=500, srcEnd=1000, speed=1.0),
        Segment(srcStart=1000, srcEnd=1500, speed=2.0),
    ]
    out = normalize_segments(segs)
    assert out == [
        Segment(srcStart=0, srcEnd=1000, speed=1.0),
        Segment(srcStart=1000, srcEnd=1500, speed=2.0),
    ]

def test_normalize_non_adjacent_or_diff_speed():
    segs = [
        Segment(srcStart=0, srcEnd=400, speed=1.0),
        Segment(srcStart=500, srcEnd=800, speed=1.0),
        Segment(srcStart=800, srcEnd=900, speed=1.0),
    ]
    out = normalize_segments(segs)
    # first segment stays, second and third merge because they are adjacent
    assert out == [
        Segment(srcStart=0, srcEnd=400, speed=1.0),
        Segment(srcStart=500, srcEnd=900, speed=1.0),
    ]

def test_normalize_drops_zero_length():
    segs = [
        Segment(srcStart=0, srcEnd=0, speed=1.0),
        Segment(srcStart=100, srcEnd=150, speed=1.0),
    ]
    out = normalize_segments(segs)
    assert out == [Segment(srcStart=100, srcEnd=150, speed=1.0)]

# -------------------- present_ms & timeline_duration --------------------

def test_present_ms_and_timeline_duration_various_speeds():
    segs = [
        Segment(srcStart=0, srcEnd=1000, speed=1.0),   # 1 s edited
        Segment(srcStart=2000, srcEnd=3000, speed=2.0),  # 0.5 s edited
        Segment(srcStart=4000, srcEnd=5000, speed=0.5),  # 2 s edited
    ]
    r = Interval(start=500, end=4500)
    assert present_ms(segs, r) == 500 + 1000 + 500
    expected_dur = (1000 - 0) / 1.0 + (3000 - 2000) / 2.0 + (5000 - 4000) / 0.5
    assert math.isclose(timeline_duration(segs), expected_dur, rel_tol=1e-12)

# -------------------- interval_minus --------------------

def test_interval_minus_simple():
    r = Interval(start=0, end=1000)
    lst = [Interval(start=200, end=400), Interval(start=600, end=800)]
    out = interval_minus(r, lst)
    assert out == [Interval(start=0, end=200), Interval(start=400, end=600), Interval(start=800, end=1000)]

def test_interval_minus_overlapping_list():
    r = Interval(start=0, end=1000)
    lst = [Interval(start=200, end=500), Interval(start=400, end=700)]
    out = interval_minus(r, lst)
    assert out == [Interval(start=0, end=200), Interval(start=700, end=1000)]

def test_interval_minus_full_removal():
    r = Interval(start=0, end=1000)
    lst = [Interval(start=-100, end=1500)]
    out = interval_minus(r, lst)
    assert out == []

# -------------------- overlap --------------------

def test_overlap_cases():
    a = Interval(start=100, end=500)
    b = Interval(start=300, end=700)
    assert overlap(a, b) == 200
    assert overlap(b, a) == 200
    assert overlap(Interval(start=0, end=50), Interval(start=100, end=150)) == 0
    assert overlap(Interval(start=0, end=100), Interval(start=100, end=200)) == 0

# -------------------- present_intervals --------------------

def test_present_intervals_merging_and_order():
    segs = [
        Segment(srcStart=1000, srcEnd=2000, speed=1.0),
        Segment(srcStart=1500, srcEnd=2500, speed=1.0),
        Segment(srcStart=3000, srcEnd=3500, speed=1.0),
    ]
    intervals = present_intervals(segs)
    assert intervals == [Interval(start=1000, end=2500), Interval(start=3000, end=3500)]

def test_present_intervals_empty():
    assert present_intervals([]) == []

# -------------------- place_word --------------------

def test_place_word_fully_visible():
    segs = [Segment(srcStart=0, srcEnd=2000, speed=1.0)]
    placed = layout(segs)
    w = make_word(start=500, end=1500)
    result = place_word(placed, w)
    assert result["visible"] is True
    assert math.isclose(result["tlStart"], 500.0, rel_tol=1e-12)
    assert math.isclose(result["tlEnd"], 1500.0, rel_tol=1e-12)
    assert result["keptFraction"] == 1.0

def test_place_word_partially_cut():
    # Two segments with a gap (cut) between them
    segs = [
        Segment(srcStart=0, srcEnd=800, speed=1.0),
        Segment(srcStart=1200, srcEnd=2000, speed=1.0),
    ]
    placed = layout(segs)
    # word spans 600‑1400, thus only 600‑800 and 1200‑1400 are kept (total kept 400)
    w = make_word(start=600, end=1400)
    result = place_word(placed, w)
    assert math.isclose(result["keptFraction"], 0.5, rel_tol=1e-12)
    assert result["visible"] is True
    # best overlap is the first segment (tie broken by first encountered)
    assert math.isclose(result["tlStart"], 600.0, rel_tol=1e-12)
    assert math.isclose(result["tlEnd"], 800.0, rel_tol=1e-12)

def test_place_word_completely_removed():
    segs = [Segment(srcStart=0, srcEnd=500, speed=1.0)]
    placed = layout(segs)
    w = make_word(start=600, end=800)
    result = place_word(placed, w)
    assert result["visible"] is False
    assert result["keptFraction"] == 0.0
    assert result["tlStart"] is None
    assert result["tlEnd"] is None

def test_place_word_after_speed_change():
    segs = [
        Segment(srcStart=0, srcEnd=1000, speed=2.0),   # edited duration = 500
        Segment(srcStart=1000, srcEnd=2000, speed=0.5),  # edited duration = 2000
    ]
    placed = layout(segs)
    w = make_word(start=500, end=1500)
    result = place_word(placed, w)
    # Overlaps are equal; best is first segment per TS implementation
    assert result["visible"] is True
    assert result["keptFraction"] == 1.0
    assert math.isclose(result["tlStart"], 250.0, rel_tol=1e-12)  # (500-0)/2
    assert math.isclose(result["tlEnd"], 500.0, rel_tol=1e-12)   # (1000-0)/2

# -------------------- reordered segments --------------------

def test_place_word_with_reordered_segments():
    # Segments are out of source order; layout keeps given order
    segs = [
        Segment(srcStart=20_000, srcEnd=30_000, speed=2.0),
        Segment(srcStart=0, srcEnd=10_000, speed=1.0),
    ]
    placed = layout(segs)
    # word fully inside the first segment (source 22k‑28k)
    w = make_word(start=22_000, end=28_000)
    result = place_word(placed, w)
    # First segment edited duration = (10k)/2 = 5k, tlStart=0
    assert math.isclose(result["tlStart"], (22_000 - 20_000) / 2.0, rel_tol=1e-12)
    assert math.isclose(result["tlEnd"], (28_000 - 20_000) / 2.0, rel_tol=1e-12)

# -------------------- edge cases for place_word --------------------

def test_place_word_empty_word_interval():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    placed = layout(segs)
    w = make_word(start=500, end=500)  # zero‑length word
    result = place_word(placed, w)
    # dur = max(1, 0) => 1, kept = 0, so invisible
    assert result["visible"] is False
    assert result["keptFraction"] == 0.0
    assert result["tlStart"] is None
    assert result["tlEnd"] is None

def test_place_word_floating_speed_precision():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=0.75)]
    placed = layout(segs)
    w = make_word(start=0, end=1000)
    result = place_word(placed, w)
    assert result["visible"] is True
    # tlEnd should be edited duration = (1000)/0.75
    assert math.isclose(result["tlEnd"], 1000 / 0.75, rel_tol=1e-12)

# -------------------- immutability checks --------------------

def _deepcopy_check(func, *args):
    copies = [copy.deepcopy(arg) for arg in args]
    _ = func(*args)
    for original, copy_ in zip(args, copies):
        assert original == copy_, f"{func.__name__} mutated its input"

def test_immutability_split_at():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    _deepcopy_check(split_at, segs, 500)

def test_immutability_subtract_range():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    r = Interval(start=200, end=800)
    _deepcopy_check(subtract_range, segs, r)

def test_immutability_normalize_segments():
    segs = [Segment(srcStart=0, srcEnd=500, speed=1.0), Segment(srcStart=500, srcEnd=1000, speed=1.0)]
    _deepcopy_check(normalize_segments, segs)

def test_immutability_present_ms():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    r = Interval(start=100, end=200)
    _deepcopy_check(present_ms, segs, r)

def test_immutability_timeline_duration():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    _deepcopy_check(timeline_duration, segs)

def test_immutability_interval_minus():
    r = Interval(start=0, end=1000)
    lst = [Interval(start=200, end=300)]
    _deepcopy_check(interval_minus, r, lst)

def test_immutability_place_word():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    placed = layout(segs)
    w = make_word(100, 200)
    _deepcopy_check(place_word, placed, w)

# -------------------- place_word_slices tests --------------------

def test_place_word_slices_single_fully_visible():
    segs = [Segment(srcStart=0, srcEnd=2000, speed=1.0)]
    placed = layout(segs)
    w = make_word(start=500, end=1500)
    res = place_word_slices(placed, w)
    assert res.visible is True
    assert res.keptFraction == 1.0
    assert res.totalKeptMs == 1000
    assert len(res.slices) == 1
    sl = res.slices[0]
    assert sl.srcStart == 500
    assert sl.srcEnd == 1500
    assert sl.tlStart == 500.0
    assert sl.tlEnd == 1500.0
    assert sl.speed == 1.0
    assert sl.segmentIndex == 0

def test_place_word_slices_multiple_surviving_slices_across_cut():
    # Gap 800-1200 cut out; word spans 600-1400 (dur=800)
    segs = [
        Segment(srcStart=0, srcEnd=800, speed=1.0),
        Segment(srcStart=1200, srcEnd=2000, speed=1.0),
    ]
    placed = layout(segs)
    w = make_word(start=600, end=1400)
    res = place_word_slices(placed, w)
    # Proves that multiple surviving timeline slices are returned
    assert len(res.slices) == 2
    assert res.totalKeptMs == 400
    assert math.isclose(res.keptFraction, 0.5, rel_tol=1e-12)
    assert res.visible is True

    # First slice in first segment
    s0 = res.slices[0]
    assert s0.srcStart == 600
    assert s0.srcEnd == 800
    assert math.isclose(s0.tlStart, 600.0, rel_tol=1e-12)
    assert math.isclose(s0.tlEnd, 800.0, rel_tol=1e-12)
    assert s0.segmentIndex == 0

    # Second slice in second segment (which starts at tl 800)
    s1 = res.slices[1]
    assert s1.srcStart == 1200
    assert s1.srcEnd == 1400
    assert math.isclose(s1.tlStart, 800.0, rel_tol=1e-12)
    assert math.isclose(s1.tlEnd, 1000.0, rel_tol=1e-12)
    assert s1.segmentIndex == 1

def test_place_word_slices_different_speeds():
    segs = [
        Segment(srcStart=0, srcEnd=1000, speed=2.0),     # tl: 0 - 500
        Segment(srcStart=1000, srcEnd=2000, speed=0.5),  # tl: 500 - 2500
    ]
    placed = layout(segs)
    w = make_word(start=500, end=1500)
    res = place_word_slices(placed, w)
    assert len(res.slices) == 2
    assert res.totalKeptMs == 1000
    assert res.keptFraction == 1.0

    # Slice 0 in 2x segment
    s0 = res.slices[0]
    assert s0.srcStart == 500
    assert s0.srcEnd == 1000
    assert math.isclose(s0.tlStart, 250.0, rel_tol=1e-12)
    assert math.isclose(s0.tlEnd, 500.0, rel_tol=1e-12)
    assert s0.speed == 2.0

    # Slice 1 in 0.5x segment
    s1 = res.slices[1]
    assert s1.srcStart == 1000
    assert s1.srcEnd == 1500
    assert math.isclose(s1.tlStart, 500.0, rel_tol=1e-12)
    assert math.isclose(s1.tlEnd, 1500.0, rel_tol=1e-12)
    assert s1.speed == 0.5

def test_place_word_slices_reordered_segments():
    segs = [
        Segment(srcStart=20_000, srcEnd=30_000, speed=1.0),  # tl: 0 - 10000
        Segment(srcStart=0, srcEnd=10_000, speed=1.0),       # tl: 10000 - 20000
    ]
    placed = layout(segs)
    # Word spans source 8000 to 22000 (crosses both segments in reverse timeline order)
    w = make_word(start=8_000, end=22_000)
    res = place_word_slices(placed, w)
    assert len(res.slices) == 2

    # Slices appear in timeline order!
    # First timeline slice is in segment 0 (src 20000-22000)
    assert res.slices[0].srcStart == 20_000
    assert res.slices[0].srcEnd == 22_000
    assert res.slices[0].tlStart == 0.0
    assert res.slices[0].tlEnd == 2_000.0
    assert res.slices[0].segmentIndex == 0

    # Second timeline slice is in segment 1 (src 8000-10000)
    assert res.slices[1].srcStart == 8_000
    assert res.slices[1].srcEnd == 10_000
    assert res.slices[1].tlStart == 18_000.0
    assert res.slices[1].tlEnd == 20_000.0
    assert res.slices[1].segmentIndex == 1

def test_place_word_slices_fully_cut():
    segs = [Segment(srcStart=0, srcEnd=500, speed=1.0)]
    placed = layout(segs)
    w = make_word(start=600, end=800)
    res = place_word_slices(placed, w)
    assert res.visible is False
    assert res.keptFraction == 0.0
    assert res.totalKeptMs == 0
    assert len(res.slices) == 0

def test_place_word_slices_empty_word():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    placed = layout(segs)
    w = make_word(start=500, end=500)
    res = place_word_slices(placed, w)
    assert res.visible is False
    assert len(res.slices) == 0

def test_place_words_slices_batch():
    segs = [Segment(srcStart=0, srcEnd=2000, speed=1.0)]
    placed = layout(segs)
    w1 = make_word(100, 500, "w1")
    w2 = make_word(600, 1000, "w2")
    res_map = place_words_slices(placed, [w1, w2])
    assert "w1" in res_map and "w2" in res_map
    assert res_map["w1"].visible is True
    assert res_map["w2"].visible is True

def test_place_word_compatibility_preserved():
    # Verify legacy place_word continues to work as expected
    segs = [Segment(srcStart=0, srcEnd=2000, speed=1.0)]
    placed = layout(segs)
    w = make_word(500, 1500)
    res = place_word(placed, w)
    assert isinstance(res, dict)
    assert res["visible"] is True
    assert math.isclose(res["tlStart"], 500.0)
    assert math.isclose(res["tlEnd"], 1500.0)
    assert res["speed"] == 1.0

def test_immutability_place_word_slices():
    segs = [Segment(srcStart=0, srcEnd=1000, speed=1.0)]
    placed = layout(segs)
    w = make_word(100, 200)
    _deepcopy_check(place_word_slices, placed, w)
