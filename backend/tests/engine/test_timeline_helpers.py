"""""""""Tests for the low‑level timeline helper functions.

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
"""""""""

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
)

# Helper to create a word spanning a given interval
def make_word(start: Ms, end: Ms, word_id: str = """w1""") -> Word:
    return Word(id=word_id, index=0, text="""test""", start=start, end=end)

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
    # three segments with different speeds
    segs = [
        Segment(srcStart=0, srcEnd=1000, speed=1.0),   # 1 s edited
        Segment(srcStart=2000, srcEnd=3000, speed=2.0),  # 0.5 s edited
        Segment(srcStart=4000, srcEnd=5000, speed=0.5),  # 2 s edited
    ]
    # interval that overlaps all three
    r = Interval(start=500, end=4500)
    # manually compute expected present ms: overlap with seg1 = 500, seg2 = 1000, seg3 = 500
    assert present_ms(segs, r) == 500 + 1000 + 500
    # timeline duration should be sum of edited lengths
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
    # merged removal yields 200‑700 removed
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
    assert overlap(a, b) == 200  # 300‑500
    assert overlap(b, a) == 200
    # non‑overlap
    assert overlap(Interval(start=0, end=50), Interval(start=100, end=150)) == 0
    # touching edges – should be 0
    assert overlap(Interval(start=0, end=100), Interval(start=100, end=200)) == 0

# -------------------- present_intervals --------------------

def test_present_intervals_merging_and_order():
    segs = [
        Segment(srcStart=1000, srcEnd=2000, speed=1.0),
        Segment(srcStart=1500, srcEnd=2500, speed=1.0),  # overlaps previous
        Segment(srcStart=3000, srcEnd=3500, speed=1.0),
    ]
    intervals = present_intervals(segs)
    # Expected merged intervals: 1000‑2500 and 3000‑3500
    assert intervals == [Interval(start=1000, end=2500), Interval(start=3000, end=3500)]

def test_present_intervals_empty():
    assert present_intervals([]) == []

# -------------------- place_word --------------------

def test_place_word_fully_visible():
    # simple timeline with one segment covering the word fully
    segs = [Segment(srcStart=0, srcEnd=2000, speed=1.0)]
    placed = []  # we will use layout indirectly via place_word expects PlacedSegment list
    from backend.app.engine.timeline import layout
    placed = layout(segs)
    w = make_word(start=500, end=1500)
    result = place_word(placed, w)
    assert result["""visible"""] is True
    # tlStart should be same offset as source because speed=1
    assert math.isclose(result["""tlStart"""], 500.0, rel_tol=1e-12)
    assert math.isclose(result["""tlEnd"""], 1500.0, rel_tol=1e-12)
    assert result["""keptFraction"""] == 1.0

def test_place_word_partially_cut():
    # Two segments, second segment cuts out part of the word
    segs = [
        Segment(srcStart=0, srcEnd=800, speed=1.0),
        Segment(srcStart=1200, srcEnd=2000, speed=1.0),
    ]
    placed = layout(segs)
    # word spans 600‑1400, so only 600‑800 and 1200‑1400 are kept (total kept 400)
    w = make_word(start=600, end=1400)
    result = place_word(placed, w)
    # kept fraction = 400 / (1400‑600)=0.5, threshold is 0.5 ⇒ visible
    assert math.isclose(result["""keptFraction"""], 0.5, rel_tol=1e-12)
    assert result["""visible"""] is True
    # tlStart should be from first kept slice (600‑800)
    assert math.isclose(result["""tlStart"""], 600.0, rel_tol=1e-12)
    # tlEnd should be from second kept slice (1200‑1400) mapped linearly
    assert math.isclose(result["""tlEnd"""], 1200.0 + (1400 - 1200), rel_tol=1e-12)

def test_place_word_completely_removed():
    segs = [Segment(srcStart=0, srcEnd=500, speed=1.0)]
    placed = layout(segs)
    w = make_word(start=600, end=800)
    result = place_word(placed, w)
    assert result["""visible"""] is False
    assert result["""keptFraction"""] == 0.0
    assert result["""tlStart"""] is None
    assert result["""tlEnd"""] is None

def test_place_word_after_speed_change():
    # speed up first half, slow second half
    segs = [
        Segment(srcStart=0, srcEnd=1000, speed=2.0),   # edited duration = 500
        Segment(srcStart=1000, srcEnd=2000, speed=0.5),  # edited duration = 2000
    ]
    placed = layout(segs)
    w = make_word(start=500, end=1500)
    result = place_word(placed, w)
    # Word overlaps both segments: 500‑1000 (fast) and 1000‑1500 (slow)
    # keptFraction = (500 + 500) / 1000 = 1.0
    assert result["""visible"""] is True
    assert result["""keptFraction"""] == 1.0
    # tlStart from fast segment: tlStart of first segment is 0, offset = (500‑0)/2 = 250
    assert math.isclose(result["""tlStart"""], 250.0, rel_tol=1e-12)
    # tlEnd from slow segment: second segment tlStart = 500, offset = (1500‑1000)/0.5 = 1000 -> 500+1000 = 1500
    assert math.isclose(result["""tlEnd"""], 1500.0, rel_tol=1e-12)

# -------------------- immutability checks --------------------

def _deepcopy_check(func, *args):
    # make deep copies of mutable arguments and ensure they are unchanged after call
    copies = [copy.deepcopy(arg) for arg in args]
    _ = func(*args)
    for original, copy_ in zip(args, copies):
        assert original == copy_, f"""{func.__name__} mutated its input"""

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
"""