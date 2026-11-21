"""
Comprehensive tests for ``backend.app.engine.operations``.

Coverage targets
----------------
* Every operation type: cut, restore, speed, mute, bleep, clear_audio,
  add_caption, remove_caption, style_caption, move.
* Valid (happy-path), invalid (error) and boundary cases for each.
* Multi-operation apply_ops: total success, first-op failure, mid-chain failure.
* Immutability: input state must never be mutated.
* Repeatability: same input + op → same output every time.
"""

from __future__ import annotations

import copy
from typing import Dict

import pytest

from backend.app.engine.types import (
    AudioEffect,
    Caption,
    EngineError,
    Interval,
    Segment,
    TimelineState,
    Word,
)
from backend.app.engine.operations import (
    MIN_SPEED,
    MAX_SPEED,
    MIN_TIMELINE_MS,
    apply_op,
    apply_ops,
)

# ===========================================================================
# Fixture helpers
# ===========================================================================

def make_state(
    duration: int = 10_000,
    segments=None,
    captions=None,
    effects=None,
    next_id: int = 1,
) -> TimelineState:
    """Return a minimal TimelineState. Default: single 10 s segment at 1x."""
    if segments is None:
        segments = [Segment(srcStart=0, srcEnd=duration, speed=1.0)]
    return TimelineState(
        sourceDurationMs=duration,
        segments=list(segments),
        captions=list(captions or []),
        effects=list(effects or []),
        nextId=next_id,
    )


def make_word(wid: str, start: int, end: int, index: int = 0) -> Word:
    return Word(id=wid, index=index, text=wid, start=start, end=end)


def make_words(*pairs) -> Dict[str, Word]:
    """make_words(('w1', 0, 500), ('w2', 500, 1000)) → {id: Word}."""
    out: Dict[str, Word] = {}
    for i, (wid, s, e) in enumerate(pairs):
        out[wid] = make_word(wid, s, e, index=i)
    return out


EMPTY_WORDS: Dict[str, Word] = {}


def assert_ok(result, msg="expected ok result") -> TimelineState:
    assert result["ok"] is True, f"{msg}: got error {result.get('error')}"
    return result["state"]


def assert_fail(result, code: str = None) -> EngineError:
    assert result["ok"] is False, "expected failure result"
    err = result["error"]
    if code is not None:
        assert err.code == code, f"expected code {code!r}, got {err.code!r}"
    return err


def assert_immutable(original: TimelineState, after_op_state: TimelineState):
    """The two state objects must be independent (not sharing list references)."""
    assert original.segments is not after_op_state.segments
    assert original.captions is not after_op_state.captions
    assert original.effects is not after_op_state.effects


# ===========================================================================
# CUT
# ===========================================================================

class TestCut:

    def test_cut_middle_removes_range(self):
        state = make_state(duration=10_000)
        op = {"type": "cut", "ranges": [Interval(start=2_000, end=5_000)]}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        # Remaining source: 0–2000 and 5000–10000 → 7 s
        assert len(new.segments) == 2
        assert new.segments[0] == Segment(srcStart=0, srcEnd=2_000, speed=1.0)
        assert new.segments[1] == Segment(srcStart=5_000, srcEnd=10_000, speed=1.0)

    def test_cut_note_message(self):
        state = make_state(duration=10_000)
        op = {"type": "cut", "ranges": [Interval(start=0, end=3_000)]}
        result = apply_op(state, op, EMPTY_WORDS)
        assert result["ok"] is True
        assert "3.000s" in result["notes"][0]

    def test_cut_no_ranges_error(self):
        state = make_state()
        op = {"type": "cut", "ranges": []}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "INVALID_OPERATION")

    def test_cut_already_removed_no_change(self):
        # Pre-cut 2000–5000, then try to cut it again.
        state = make_state(segments=[
            Segment(srcStart=0, srcEnd=2_000, speed=1.0),
            Segment(srcStart=5_000, srcEnd=10_000, speed=1.0),
        ])
        op = {"type": "cut", "ranges": [Interval(start=2_000, end=5_000)]}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "NO_CHANGE")

    def test_cut_whole_video_rejected(self):
        state = make_state(duration=10_000)
        op = {"type": "cut", "ranges": [Interval(start=0, end=10_000)]}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "WHOLE_VIDEO_CUT")

    def test_cut_leaving_exactly_min_duration(self):
        # Leave exactly MIN_TIMELINE_MS (100 ms) of content.
        state = make_state(duration=10_000)
        op = {"type": "cut", "ranges": [Interval(start=0, end=9_900)]}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        assert len(new.segments) == 1
        assert new.segments[0].srcEnd - new.segments[0].srcStart == 100

    def test_cut_leaving_less_than_min_rejected(self):
        # Leave only 99 ms.
        state = make_state(duration=10_000)
        op = {"type": "cut", "ranges": [Interval(start=0, end=9_901)]}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "WHOLE_VIDEO_CUT")

    def test_cut_range_out_of_bounds(self):
        state = make_state(duration=5_000)
        op = {"type": "cut", "ranges": [Interval(start=0, end=6_000)]}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "RANGE_OUT_OF_BOUNDS")

    def test_cut_non_integer_range_error(self):
        state = make_state()
        op = {"type": "cut", "ranges": [Interval(start=0.5, end=1_000)]}  # type: ignore
        assert_fail(apply_op(state, op, EMPTY_WORDS), "INVALID_RANGE")

    def test_cut_multiple_ranges(self):
        state = make_state(duration=10_000)
        op = {
            "type": "cut",
            "ranges": [Interval(start=0, end=1_000), Interval(start=5_000, end=6_000)],
        }
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        # Remaining: 1000–5000, 6000–10000
        total = sum(s.srcEnd - s.srcStart for s in new.segments)
        assert total == 8_000

    def test_cut_immutability(self):
        state = make_state()
        original = copy.deepcopy(state)
        op = {"type": "cut", "ranges": [Interval(start=0, end=1_000)]}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        assert state.segments == original.segments
        assert_immutable(state, new)

    def test_cut_repeatability(self):
        state = make_state()
        op = {"type": "cut", "ranges": [Interval(start=1_000, end=3_000)]}
        r1 = apply_op(state, op, EMPTY_WORDS)
        r2 = apply_op(state, op, EMPTY_WORDS)
        assert r1["state"].segments == r2["state"].segments  # type: ignore[index]


# ===========================================================================
# RESTORE
# ===========================================================================

class TestRestore:

    def _pre_cut_state(self) -> TimelineState:
        """State with 2000–5000 already cut."""
        return make_state(segments=[
            Segment(srcStart=0, srcEnd=2_000, speed=1.0),
            Segment(srcStart=5_000, srcEnd=10_000, speed=1.0),
        ])

    def test_restore_missing_range(self):
        state = self._pre_cut_state()
        op = {"type": "restore", "ranges": [Interval(start=2_000, end=5_000)]}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        assert new.segments == [Segment(srcStart=0, srcEnd=10_000, speed=1.0)]

    def test_restore_nothing_cut_error(self):
        state = make_state()
        op = {"type": "restore", "ranges": [Interval(start=0, end=5_000)]}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "NO_CHANGE")

    def test_restore_partial_range(self):
        state = self._pre_cut_state()
        # Only restore the first half of the cut (2000–3500).
        op = {"type": "restore", "ranges": [Interval(start=2_000, end=3_500)]}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        total = sum(s.srcEnd - s.srcStart for s in new.segments)
        assert total == 7_000 + 1_500

    def test_restore_at_original_position(self):
        state = self._pre_cut_state()
        op = {"type": "restore", "ranges": [Interval(start=2_000, end=5_000)]}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        # After restore the segments should be re-merged into one contiguous span.
        assert len(new.segments) == 1

    def test_restore_note_message(self):
        state = self._pre_cut_state()
        op = {"type": "restore", "ranges": [Interval(start=2_000, end=5_000)]}
        result = apply_op(state, op, EMPTY_WORDS)
        assert "3.000s" in result["notes"][0]  # type: ignore[index]

    def test_restore_range_out_of_bounds(self):
        state = self._pre_cut_state()
        op = {"type": "restore", "ranges": [Interval(start=0, end=99_000)]}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "RANGE_OUT_OF_BOUNDS")

    def test_restore_immutability(self):
        state = self._pre_cut_state()
        original = copy.deepcopy(state)
        op = {"type": "restore", "ranges": [Interval(start=2_000, end=5_000)]}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        assert state.segments == original.segments
        assert_immutable(state, new)


# ===========================================================================
# SPEED
# ===========================================================================

class TestSpeed:

    def test_speed_change_middle(self):
        state = make_state(duration=10_000)
        op = {"type": "speed", "range": Interval(start=2_000, end=6_000), "speed": 2.0}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        speeds = [(s.srcStart, s.srcEnd, s.speed) for s in new.segments]
        assert (2_000, 6_000, 2.0) in speeds

    def test_speed_below_min_rejected(self):
        state = make_state()
        op = {"type": "speed", "range": Interval(start=0, end=5_000), "speed": MIN_SPEED - 0.01}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "INVALID_SPEED")

    def test_speed_above_max_rejected(self):
        state = make_state()
        op = {"type": "speed", "range": Interval(start=0, end=5_000), "speed": MAX_SPEED + 0.01}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "INVALID_SPEED")

    def test_speed_at_min_allowed(self):
        state = make_state()
        op = {"type": "speed", "range": Interval(start=0, end=5_000), "speed": MIN_SPEED}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        assert new.segments[0].speed == MIN_SPEED

    def test_speed_at_max_allowed(self):
        state = make_state()
        op = {"type": "speed", "range": Interval(start=0, end=5_000), "speed": MAX_SPEED}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        assert new.segments[0].speed == MAX_SPEED

    def test_speed_already_at_that_speed(self):
        state = make_state()
        op = {"type": "speed", "range": Interval(start=0, end=10_000), "speed": 1.0}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "NO_CHANGE")

    def test_speed_on_cut_range(self):
        state = make_state(segments=[
            Segment(srcStart=0, srcEnd=2_000, speed=1.0),
            Segment(srcStart=5_000, srcEnd=10_000, speed=1.0),
        ])
        op = {"type": "speed", "range": Interval(start=2_000, end=5_000), "speed": 2.0}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "NO_VISIBLE_CONTENT")

    def test_speed_range_out_of_bounds(self):
        state = make_state(duration=5_000)
        op = {"type": "speed", "range": Interval(start=0, end=6_000), "speed": 2.0}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "RANGE_OUT_OF_BOUNDS")

    def test_speed_normalizes_adjacent_same_speed(self):
        state = make_state(duration=10_000)
        # Set 0–5000 to 2x, then set 5000–10000 to 2x; expect one merged segment.
        op1 = {"type": "speed", "range": Interval(start=0, end=5_000), "speed": 2.0}
        s1 = assert_ok(apply_op(state, op1, EMPTY_WORDS))
        op2 = {"type": "speed", "range": Interval(start=5_000, end=10_000), "speed": 2.0}
        s2 = assert_ok(apply_op(s1, op2, EMPTY_WORDS))
        assert len(s2.segments) == 1
        assert s2.segments[0].speed == 2.0

    def test_speed_immutability(self):
        state = make_state()
        original = copy.deepcopy(state)
        op = {"type": "speed", "range": Interval(start=0, end=5_000), "speed": 2.0}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        assert state.segments == original.segments
        assert_immutable(state, new)

    def test_speed_repeatability(self):
        state = make_state()
        op = {"type": "speed", "range": Interval(start=0, end=5_000), "speed": 2.0}
        r1 = apply_op(state, op, EMPTY_WORDS)
        r2 = apply_op(state, op, EMPTY_WORDS)
        assert r1["state"].segments == r2["state"].segments  # type: ignore[index]


# ===========================================================================
# MUTE / BLEEP / CLEAR_AUDIO
# ===========================================================================

class TestAudio:

    WORDS = make_words(("w1", 0, 500), ("w2", 500, 1_000))

    def test_mute_adds_effect(self):
        state = make_state()
        op = {"type": "mute", "wordIds": ["w1"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert len(new.effects) == 1
        assert new.effects[0].kind == "mute"
        assert new.effects[0].wordId == "w1"

    def test_mute_note_message(self):
        state = make_state()
        op = {"type": "mute", "wordIds": ["w1"]}
        result = apply_op(state, op, self.WORDS)
        assert "Muted 1 word(s)" in result["notes"][0]  # type: ignore[index]

    def test_bleep_adds_effect(self):
        state = make_state()
        op = {"type": "bleep", "wordIds": ["w1"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert new.effects[0].kind == "bleep"

    def test_mute_duplicate_no_change(self):
        state = make_state(effects=[
            AudioEffect(id="fx_1", kind="mute", wordId="w1", srcStart=0, srcEnd=500)
        ])
        op = {"type": "mute", "wordIds": ["w1"]}
        assert_fail(apply_op(state, op, self.WORDS), "NO_CHANGE")

    def test_bleep_duplicate_no_change(self):
        state = make_state(effects=[
            AudioEffect(id="fx_1", kind="bleep", wordId="w1", srcStart=0, srcEnd=500)
        ])
        op = {"type": "bleep", "wordIds": ["w1"]}
        assert_fail(apply_op(state, op, self.WORDS), "NO_CHANGE")

    def test_mute_unknown_word(self):
        state = make_state()
        op = {"type": "mute", "wordIds": ["NONEXISTENT"]}
        assert_fail(apply_op(state, op, self.WORDS), "UNKNOWN_WORD")

    def test_clear_audio_removes_effect(self):
        state = make_state(effects=[
            AudioEffect(id="fx_1", kind="mute", wordId="w1", srcStart=0, srcEnd=500)
        ])
        op = {"type": "clear_audio", "wordIds": ["w1"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert len(new.effects) == 0

    def test_clear_audio_nothing_to_clear(self):
        state = make_state()
        op = {"type": "clear_audio", "wordIds": ["w1"]}
        assert_fail(apply_op(state, op, self.WORDS), "NO_CHANGE")

    def test_clear_audio_multiple_words(self):
        state = make_state(effects=[
            AudioEffect(id="fx_1", kind="mute", wordId="w1", srcStart=0, srcEnd=500),
            AudioEffect(id="fx_2", kind="bleep", wordId="w2", srcStart=500, srcEnd=1_000),
        ])
        op = {"type": "clear_audio", "wordIds": ["w1", "w2"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert len(new.effects) == 0

    def test_mute_multiple_words_partial_already_muted(self):
        # w1 already muted; w2 not → should add w2 and report 1 added.
        state = make_state(effects=[
            AudioEffect(id="fx_1", kind="mute", wordId="w1", srcStart=0, srcEnd=500)
        ])
        op = {"type": "mute", "wordIds": ["w1", "w2"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert len(new.effects) == 2
        assert "1 word(s)" in new.effects[-1].wordId or "Muted 1" in apply_op(state, op, self.WORDS)["notes"][0]  # type: ignore

    def test_mute_nextId_incremented(self):
        state = make_state(next_id=5)
        op = {"type": "mute", "wordIds": ["w1"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert new.nextId == 6
        assert new.effects[0].id == "fx_5"

    def test_mute_immutability(self):
        state = make_state()
        original = copy.deepcopy(state)
        op = {"type": "mute", "wordIds": ["w1"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert state.effects == original.effects
        assert_immutable(state, new)


# ===========================================================================
# ADD_CAPTION
# ===========================================================================

class TestAddCaption:

    WORDS = make_words(("w1", 0, 500), ("w2", 500, 1_000), ("w3", 1_000, 1_500))

    def test_add_caption_basic(self):
        state = make_state()
        op = {"type": "add_caption", "text": "Hello world", "wordIds": ["w1", "w2"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert len(new.captions) == 1
        cap = new.captions[0]
        assert cap.text == "Hello world"
        assert cap.size == "md"
        assert cap.position == "bottom"

    def test_add_caption_sorts_word_ids_by_index(self):
        state = make_state()
        # Pass in reverse order; should be stored sorted by transcript index.
        op = {"type": "add_caption", "text": "X", "wordIds": ["w3", "w1"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert new.captions[0].wordIds == ["w1", "w3"]

    def test_add_caption_empty_text_rejected(self):
        state = make_state()
        op = {"type": "add_caption", "text": "   ", "wordIds": ["w1"]}
        assert_fail(apply_op(state, op, self.WORDS), "INVALID_CAPTION")

    def test_add_caption_text_too_long(self):
        state = make_state()
        op = {"type": "add_caption", "text": "A" * 121, "wordIds": ["w1"]}
        assert_fail(apply_op(state, op, self.WORDS), "INVALID_CAPTION")

    def test_add_caption_text_max_length_ok(self):
        state = make_state()
        op = {"type": "add_caption", "text": "A" * 120, "wordIds": ["w1"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert len(new.captions[0].text) == 120

    def test_add_caption_unknown_word(self):
        state = make_state()
        op = {"type": "add_caption", "text": "Hi", "wordIds": ["NOPE"]}
        assert_fail(apply_op(state, op, self.WORDS), "UNKNOWN_WORD")

    def test_add_caption_duplicate_rejected(self):
        state = make_state()
        op = {"type": "add_caption", "text": "Hi", "wordIds": ["w1"]}
        s1 = assert_ok(apply_op(state, op, self.WORDS))
        assert_fail(apply_op(s1, op, self.WORDS), "NO_CHANGE")

    def test_add_caption_note_on_cut_word(self):
        # Cut 0–500 so w1 is invisible.
        state = make_state(segments=[
            Segment(srcStart=500, srcEnd=10_000, speed=1.0)
        ])
        op = {"type": "add_caption", "text": "Hi", "wordIds": ["w1"]}
        result = apply_op(state, op, self.WORDS)
        assert result["ok"] is True
        assert len(result["notes"]) == 1
        assert "cut" in result["notes"][0]

    def test_add_caption_id_uses_nextid(self):
        state = make_state(next_id=10)
        op = {"type": "add_caption", "text": "Hi", "wordIds": ["w1"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert new.captions[0].id == "cap_10"
        assert new.nextId == 11

    def test_add_caption_immutability(self):
        state = make_state()
        original = copy.deepcopy(state)
        op = {"type": "add_caption", "text": "Hi", "wordIds": ["w1"]}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert state.captions == original.captions
        assert_immutable(state, new)


# ===========================================================================
# REMOVE_CAPTION
# ===========================================================================

class TestRemoveCaption:

    WORDS = make_words(("w1", 0, 500))

    def _state_with_caption(self) -> TimelineState:
        cap = Caption(id="cap_1", text="Hi", wordIds=["w1"], size="md", position="bottom")
        return make_state(captions=[cap])

    def test_remove_existing_caption(self):
        state = self._state_with_caption()
        op = {"type": "remove_caption", "captionId": "cap_1"}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert len(new.captions) == 0

    def test_remove_nonexistent_caption(self):
        state = self._state_with_caption()
        op = {"type": "remove_caption", "captionId": "cap_999"}
        assert_fail(apply_op(state, op, self.WORDS), "UNKNOWN_CAPTION")

    def test_remove_caption_immutability(self):
        state = self._state_with_caption()
        original = copy.deepcopy(state)
        op = {"type": "remove_caption", "captionId": "cap_1"}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert len(state.captions) == 1
        assert_immutable(state, new)


# ===========================================================================
# STYLE_CAPTION
# ===========================================================================

class TestStyleCaption:

    WORDS = make_words(("w1", 0, 500))

    def _state_with_caption(self, size="md", position="bottom") -> TimelineState:
        cap = Caption(id="cap_1", text="Hi", wordIds=["w1"], size=size, position=position)  # type: ignore[arg-type]
        return make_state(captions=[cap])

    def test_style_size_step_up(self):
        state = self._state_with_caption(size="md")
        op = {"type": "style_caption", "captionId": "cap_1", "sizeStep": 1}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert new.captions[0].size == "lg"

    def test_style_size_step_down(self):
        state = self._state_with_caption(size="md")
        op = {"type": "style_caption", "captionId": "cap_1", "sizeStep": -1}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert new.captions[0].size == "sm"

    def test_style_size_step_at_max_clamped(self):
        # xl is max; stepping up must have no effect → no change.
        state = self._state_with_caption(size="xl")
        op = {"type": "style_caption", "captionId": "cap_1", "sizeStep": 1}
        assert_fail(apply_op(state, op, self.WORDS), "NO_CHANGE")

    def test_style_size_step_at_min_clamped(self):
        state = self._state_with_caption(size="sm")
        op = {"type": "style_caption", "captionId": "cap_1", "sizeStep": -1}
        assert_fail(apply_op(state, op, self.WORDS), "NO_CHANGE")

    def test_style_position_change(self):
        state = self._state_with_caption(position="bottom")
        op = {"type": "style_caption", "captionId": "cap_1", "position": "top"}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert new.captions[0].position == "top"

    def test_style_same_position_no_change(self):
        state = self._state_with_caption(position="bottom")
        op = {"type": "style_caption", "captionId": "cap_1", "position": "bottom"}
        assert_fail(apply_op(state, op, self.WORDS), "NO_CHANGE")

    def test_style_unknown_caption(self):
        state = self._state_with_caption()
        op = {"type": "style_caption", "captionId": "cap_999", "position": "top"}
        assert_fail(apply_op(state, op, self.WORDS), "UNKNOWN_CAPTION")

    def test_style_does_not_mutate_frozen_caption(self):
        """Caption is a frozen dataclass; style must replace it, not mutate it."""
        state = self._state_with_caption(size="md", position="bottom")
        original_cap = state.captions[0]
        op = {"type": "style_caption", "captionId": "cap_1", "position": "top"}
        new = assert_ok(apply_op(state, op, self.WORDS))
        # Original caption object must be unchanged.
        assert original_cap.position == "bottom"
        assert new.captions[0].position == "top"

    def test_style_size_and_position_together(self):
        state = self._state_with_caption(size="md", position="bottom")
        op = {"type": "style_caption", "captionId": "cap_1", "sizeStep": 2, "position": "top"}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert new.captions[0].size == "xl"
        assert new.captions[0].position == "top"

    def test_style_immutability(self):
        state = self._state_with_caption()
        original = copy.deepcopy(state)
        op = {"type": "style_caption", "captionId": "cap_1", "position": "top"}
        new = assert_ok(apply_op(state, op, self.WORDS))
        assert state.captions[0].position == original.captions[0].position
        assert_immutable(state, new)


# ===========================================================================
# MOVE (reorder)
# ===========================================================================

class TestMove:

    def test_move_to_start(self):
        state = make_state(segments=[
            Segment(srcStart=0, srcEnd=3_000, speed=1.0),
            Segment(srcStart=3_000, srcEnd=7_000, speed=1.0),
            Segment(srcStart=7_000, srcEnd=10_000, speed=1.0),
        ])
        # Move 7000–10000 to start.
        op = {"type": "move", "range": Interval(start=7_000, end=10_000), "to": "start"}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        assert new.segments[0].srcStart == 7_000
        assert new.segments[0].srcEnd == 10_000

    def test_move_to_end(self):
        state = make_state(segments=[
            Segment(srcStart=0, srcEnd=3_000, speed=1.0),
            Segment(srcStart=3_000, srcEnd=7_000, speed=1.0),
            Segment(srcStart=7_000, srcEnd=10_000, speed=1.0),
        ])
        # Move 0–3000 to end.
        op = {"type": "move", "range": Interval(start=0, end=3_000), "to": "end"}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        last = new.segments[-1]
        assert last.srcStart == 0
        assert last.srcEnd == 3_000

    def test_move_before_src(self):
        state = make_state(segments=[
            Segment(srcStart=0, srcEnd=3_000, speed=1.0),
            Segment(srcStart=3_000, srcEnd=7_000, speed=1.0),
            Segment(srcStart=7_000, srcEnd=10_000, speed=1.0),
        ])
        # Move 7000–10000 before source position 3000.
        op = {"type": "move", "range": Interval(start=7_000, end=10_000), "to": {"beforeSrc": 3_000}}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        starts = [s.srcStart for s in new.segments]
        idx_7k = starts.index(7_000)
        idx_3k = starts.index(3_000)
        assert idx_7k < idx_3k

    def test_move_destination_inside_range_error(self):
        state = make_state()
        op = {
            "type": "move",
            "range": Interval(start=2_000, end=8_000),
            "to": {"beforeSrc": 5_000},  # inside [2000, 8000]
        }
        assert_fail(apply_op(state, op, EMPTY_WORDS), "INVALID_MOVE")

    def test_move_cut_range_error(self):
        state = make_state(segments=[
            Segment(srcStart=0, srcEnd=2_000, speed=1.0),
            Segment(srcStart=5_000, srcEnd=10_000, speed=1.0),
        ])
        op = {"type": "move", "range": Interval(start=2_000, end=5_000), "to": "start"}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "NO_VISIBLE_CONTENT")

    def test_move_whole_video_error(self):
        state = make_state(duration=10_000)
        op = {"type": "move", "range": Interval(start=0, end=10_000), "to": "start"}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "NO_CHANGE")

    def test_move_already_at_position_error(self):
        # Build a state where 0–3000 is already a distinct first segment separated by a cut,
        # so moving it to start changes nothing structurally (normalized result equals input).
        state = make_state(segments=[
            Segment(srcStart=0, srcEnd=3_000, speed=1.0),
            Segment(srcStart=5_000, srcEnd=10_000, speed=1.0),  # gap keeps them separate
        ])
        # Moving 0–3000 to "start" – it is already the first segment and can't be re-merged,
        # so normalized result equals the original.
        op = {"type": "move", "range": Interval(start=0, end=3_000), "to": "start"}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "NO_CHANGE")

    def test_move_destination_cut_error(self):
        state = make_state(segments=[
            Segment(srcStart=0, srcEnd=2_000, speed=1.0),
            Segment(srcStart=5_000, srcEnd=10_000, speed=1.0),
        ])
        # Try to move 5000–8000 before source position 3000, which is cut.
        op = {
            "type": "move",
            "range": Interval(start=5_000, end=8_000),
            "to": {"beforeSrc": 3_000},
        }
        assert_fail(apply_op(state, op, EMPTY_WORDS), "DESTINATION_CUT")

    def test_move_range_out_of_bounds(self):
        state = make_state(duration=5_000)
        op = {"type": "move", "range": Interval(start=0, end=6_000), "to": "start"}
        assert_fail(apply_op(state, op, EMPTY_WORDS), "RANGE_OUT_OF_BOUNDS")

    def test_move_immutability(self):
        state = make_state(segments=[
            Segment(srcStart=0, srcEnd=3_000, speed=1.0),
            Segment(srcStart=3_000, srcEnd=10_000, speed=1.0),
        ])
        original = copy.deepcopy(state)
        op = {"type": "move", "range": Interval(start=0, end=3_000), "to": "end"}
        new = assert_ok(apply_op(state, op, EMPTY_WORDS))
        assert state.segments == original.segments
        assert_immutable(state, new)

    def test_move_repeatability(self):
        state = make_state(segments=[
            Segment(srcStart=0, srcEnd=3_000, speed=1.0),
            Segment(srcStart=3_000, srcEnd=10_000, speed=1.0),
        ])
        op = {"type": "move", "range": Interval(start=0, end=3_000), "to": "end"}
        r1 = apply_op(state, op, EMPTY_WORDS)
        r2 = apply_op(state, op, EMPTY_WORDS)
        assert r1["state"].segments == r2["state"].segments  # type: ignore[index]


# ===========================================================================
# APPLY_OPS (multi-operation, atomic)
# ===========================================================================

class TestApplyOps:

    WORDS = make_words(("w1", 0, 500))

    def test_apply_ops_all_succeed(self):
        state = make_state(duration=10_000)
        ops = [
            {"type": "cut", "ranges": [Interval(start=0, end=1_000)]},
            {"type": "speed", "range": Interval(start=1_000, end=5_000), "speed": 2.0},
        ]
        result = apply_ops(state, ops, EMPTY_WORDS)
        assert result["ok"] is True
        assert isinstance(result["state"], TimelineState)  # type: ignore[typeddict-item]

    def test_apply_ops_first_op_fails(self):
        state = make_state(duration=10_000)
        ops = [
            {"type": "cut", "ranges": []},  # fails
            {"type": "speed", "range": Interval(start=0, end=5_000), "speed": 2.0},
        ]
        result = apply_ops(state, ops, EMPTY_WORDS)
        assert result["ok"] is False
        assert result["failedIndex"] == 0  # type: ignore[typeddict-item]

    def test_apply_ops_second_op_fails(self):
        state = make_state(duration=10_000)
        ops = [
            {"type": "cut", "ranges": [Interval(start=0, end=1_000)]},  # succeeds
            {"type": "speed", "range": Interval(start=0, end=5_000), "speed": 99.0},  # fails (speed too high)
        ]
        result = apply_ops(state, ops, EMPTY_WORDS)
        assert result["ok"] is False
        assert result["failedIndex"] == 1  # type: ignore[typeddict-item]

    def test_apply_ops_original_state_untouched(self):
        state = make_state(duration=10_000)
        original = copy.deepcopy(state)
        ops = [
            {"type": "cut", "ranges": [Interval(start=0, end=1_000)]},
            {"type": "cut", "ranges": [Interval(start=0, end=99_999)]},  # will fail
        ]
        apply_ops(state, ops, EMPTY_WORDS)
        assert state.segments == original.segments

    def test_apply_ops_notes_accumulated(self):
        state = make_state(duration=10_000)
        words = make_words(("w1", 0, 500))
        ops = [
            {"type": "cut", "ranges": [Interval(start=0, end=1_000)]},
            {"type": "mute", "wordIds": ["w1"]},
        ]
        result = apply_ops(state, ops, words)
        assert result["ok"] is True
        assert len(result["notes"]) == 2  # type: ignore[typeddict-item]

    def test_apply_ops_empty_list_returns_state(self):
        state = make_state()
        result = apply_ops(state, [], EMPTY_WORDS)
        assert result["ok"] is True
        assert result["state"].segments == state.segments  # type: ignore[typeddict-item]


# ===========================================================================
# UNKNOWN OP TYPE
# ===========================================================================

def test_unknown_op_type():
    state = make_state()
    op = {"type": "teleport"}  # type: ignore[typeddict-item]
    result = apply_op(state, op, EMPTY_WORDS)
    assert result["ok"] is False
    assert result["error"].code == "UNKNOWN_OP"


# ===========================================================================
# GLOBAL REPEATABILITY
# ===========================================================================

def test_global_repeatability_cut_speed_move():
    state = make_state(duration=10_000)
    ops = [
        {"type": "cut", "ranges": [Interval(start=5_000, end=6_000)]},
        {"type": "speed", "range": Interval(start=0, end=3_000), "speed": 2.0},
        {"type": "move", "range": Interval(start=3_000, end=5_000), "to": "end"},
    ]
    r1 = apply_ops(state, ops, EMPTY_WORDS)
    r2 = apply_ops(state, ops, EMPTY_WORDS)
    assert r1["ok"] is r2["ok"] is True
    assert r1["state"].segments == r2["state"].segments  # type: ignore[index]
    assert r1["state"].nextId == r2["state"].nextId  # type: ignore[index]
