"""Comprehensive tests for VideoFlow AI revision management and history.

Covers all Phase 1 revision management requirements:
- First revision creation (r0 initial state).
- Multiple consecutive revisions (stable, ordered IDs r0, r1, r2, ...).
- Undo once.
- Multiple consecutive undos.
- Redo once.
- Multiple consecutive redos.
- Undo followed by a new edit (clearing redo branch).
- Restore an earlier revision (creates new revision, leaves earlier intact).
- Failed operation does not create a revision (atomicity).
- Cuts and keep operations with undo/redo.
- Speed changes with undo/redo.
- Reordering with undo/redo.
- Captions and overlays with undo/redo.
- Snapshot isolation and nested mutable data (segments, captions, wordIds, overlays, style).
- RevisionManager stateful controller.
"""

from __future__ import annotations

import copy
import pytest

from backend.app.engine.types import (
    AudioEffect,
    Caption,
    EngineError,
    Interval,
    Segment,
    TextOverlay,
    TimelineState,
    Word,
)
from backend.app.engine.timeline import initial_state, timeline_hash, timeline_duration
from backend.app.engine.history import (
    RevisionManager,
    apply_and_commit,
    can_redo,
    can_undo,
    clone_timeline_state,
    commit,
    create_history,
    current_state,
    get_revision,
    head,
    redo,
    restore_revision,
    undo,
)


# ===========================================================================
# Fixture Helpers
# ===========================================================================

def make_test_state(
    duration: int = 10_000,
    segments=None,
    captions=None,
    effects=None,
    overlays=None,
    next_id: int = 1,
) -> TimelineState:
    if segments is None:
        segments = [Segment(srcStart=0, srcEnd=duration, speed=1.0)]
    return TimelineState(
        sourceDurationMs=duration,
        segments=list(segments),
        captions=list(captions or []),
        effects=list(effects or []),
        overlays=list(overlays or []),
        nextId=next_id,
    )


def make_test_words() -> dict[str, Word]:
    return {
        "w1": Word(id="w1", index=0, text="hello", start=500, end=1500),
        "w2": Word(id="w2", index=1, text="world", start=1600, end=2500),
        "w3": Word(id="w3", index=2, text="videoflow", start=3000, end=4500),
        "w4": Word(id="w4", index=3, text="studio", start=5000, end=6500),
    }


# ===========================================================================
# 1. First Revision Creation
# ===========================================================================

def test_first_revision_creation():
    state = make_test_state(duration=10_000)
    h = create_history(
        state,
        now=1000,
        label="Initial Upload",
        source_video_path="/media/video.mp4",
        source_hash="sha256_dummy_hash",
    )

    assert len(h.revisions) == 1
    r0 = h.revisions[0]
    assert r0.id == "r0"
    assert r0.index == 0
    assert r0.parentId is None
    assert r0.kind == "initial"
    assert r0.label == "Initial Upload"
    assert r0.ops == []
    assert r0.createdAt == 1000
    assert r0.hash == timeline_hash(state)
    assert len(r0.state.segments) == 1
    assert r0.state.sourceDurationMs == 10_000

    assert h.undo == ["r0"]
    assert h.redo == []
    assert head(h).id == "r0"
    assert not can_undo(h)
    assert not can_redo(h)

    # Check preserved media attributes
    assert h.sourceVideoPath == "/media/video.mp4"
    assert h.sourceHash == "sha256_dummy_hash"


# ===========================================================================
# 2. Multiple Consecutive Revisions
# ===========================================================================

def test_multiple_consecutive_revisions():
    words = make_test_words()
    state = make_test_state(duration=10_000)
    h = create_history(state, now=100)

    # Revision 1: Cut 0-2000
    cut_op = [{"type": "cut", "ranges": [Interval(start=0, end=2000)]}]
    res1 = apply_and_commit(h, cut_op, words, label="Cut intro", now=200)
    assert res1["ok"] is True
    h = res1["history"]
    r1 = res1["revision"]
    assert r1.id == "r1"
    assert r1.index == 1
    assert r1.parentId == "r0"
    assert r1.kind == "edit"
    assert head(h).id == "r1"

    # Revision 2: Speed 2x on 4000-6000
    speed_op = [{"type": "speed", "range": Interval(start=4000, end=6000), "speed": 2.0}]
    res2 = apply_and_commit(h, speed_op, words, label="Speed up", now=300)
    assert res2["ok"] is True
    h = res2["history"]
    r2 = res2["revision"]
    assert r2.id == "r2"
    assert r2.index == 2
    assert r2.parentId == "r1"
    assert head(h).id == "r2"

    # Revision 3: Add Caption
    cap_op = [{"type": "add_caption", "text": "Cool part", "wordIds": ["w3"]}]
    res3 = apply_and_commit(h, cap_op, words, label="Add caption", now=400)
    assert res3["ok"] is True
    h = res3["history"]
    r3 = res3["revision"]
    assert r3.id == "r3"
    assert r3.index == 3
    assert r3.parentId == "r2"
    assert head(h).id == "r3"

    assert len(h.revisions) == 4
    assert [r.id for r in h.revisions] == ["r0", "r1", "r2", "r3"]
    assert h.undo == ["r0", "r1", "r2", "r3"]
    assert h.redo == []
    assert can_undo(h)
    assert not can_redo(h)


# ===========================================================================
# 3. Undo Once
# ===========================================================================

def test_undo_once():
    words = make_test_words()
    h = create_history(make_test_state(), now=100)
    cut_op = [{"type": "cut", "ranges": [Interval(start=1000, end=3000)]}]
    res = apply_and_commit(h, cut_op, words, label="Cut 1-3s", now=200)
    h = res["history"]
    assert head(h).id == "r1"

    # Undo
    h, undone = undo(h)
    assert undone.id == "r1"
    assert head(h).id == "r0"
    assert h.undo == ["r0"]
    assert h.redo == ["r1"]
    assert not can_undo(h)
    assert can_redo(h)
    assert timeline_duration(head(h).state.segments) == 10_000.0


# ===========================================================================
# 4. Multiple Consecutive Undos
# ===========================================================================

def test_multiple_consecutive_undos():
    words = make_test_words()
    h = create_history(make_test_state(), now=100)

    # Create 3 revisions
    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=0, end=1000)]}], words, "Cut 1")["history"]
    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=1000, end=2000)]}], words, "Cut 2")["history"]
    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=2000, end=3000)]}], words, "Cut 3")["history"]

    assert head(h).id == "r3"
    assert h.undo == ["r0", "r1", "r2", "r3"]

    # Undo 1 -> r2
    h, u1 = undo(h)
    assert u1.id == "r3"
    assert head(h).id == "r2"
    assert h.undo == ["r0", "r1", "r2"]
    assert h.redo == ["r3"]

    # Undo 2 -> r1
    h, u2 = undo(h)
    assert u2.id == "r2"
    assert head(h).id == "r1"
    assert h.undo == ["r0", "r1"]
    assert h.redo == ["r3", "r2"]

    # Undo 3 -> r0
    h, u3 = undo(h)
    assert u3.id == "r1"
    assert head(h).id == "r0"
    assert h.undo == ["r0"]
    assert h.redo == ["r3", "r2", "r1"]
    assert not can_undo(h)

    # Attempting to undo past r0 returns EngineError (functional) and raises ValueError (when requested)
    err_undo = undo(h)
    assert isinstance(err_undo, EngineError)
    assert err_undo.code == "NOTHING_TO_UNDO"
    with pytest.raises(ValueError, match="NOTHING_TO_UNDO"):
        undo(h, raise_on_error=True)


# ===========================================================================
# 5. Redo Once
# ===========================================================================

def test_redo_once():
    words = make_test_words()
    h = create_history(make_test_state(), now=100)
    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=0, end=2000)]}], words, "Cut")["history"]

    h, _ = undo(h)
    assert head(h).id == "r0"

    h, redone = redo(h)
    assert redone.id == "r1"
    assert head(h).id == "r1"
    assert h.undo == ["r0", "r1"]
    assert h.redo == []
    assert not can_redo(h)
    assert timeline_duration(head(h).state.segments) == 8000.0


# ===========================================================================
# 6. Multiple Consecutive Redos
# ===========================================================================

def test_multiple_consecutive_redos():
    words = make_test_words()
    h = create_history(make_test_state(), now=100)

    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=0, end=1000)]}], words, "Cut 1")["history"]
    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=1000, end=2000)]}], words, "Cut 2")["history"]
    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=2000, end=3000)]}], words, "Cut 3")["history"]

    # Undo all the way to r0
    h, _ = undo(h)
    h, _ = undo(h)
    h, _ = undo(h)
    assert head(h).id == "r0"
    assert h.redo == ["r3", "r2", "r1"]

    # Redo 1 -> r1
    h, r1 = redo(h)
    assert r1.id == "r1"
    assert head(h).id == "r1"
    assert h.undo == ["r0", "r1"]
    assert h.redo == ["r3", "r2"]

    # Redo 2 -> r2
    h, r2 = redo(h)
    assert r2.id == "r2"
    assert head(h).id == "r2"
    assert h.undo == ["r0", "r1", "r2"]
    assert h.redo == ["r3"]

    # Redo 3 -> r3
    h, r3 = redo(h)
    assert r3.id == "r3"
    assert head(h).id == "r3"
    assert h.undo == ["r0", "r1", "r2", "r3"]
    assert h.redo == []

    # Attempting redo when empty returns EngineError (functional) and raises ValueError (when requested)
    err_redo = redo(h)
    assert isinstance(err_redo, EngineError)
    assert err_redo.code == "NOTHING_TO_REDO"
    with pytest.raises(ValueError, match="NOTHING_TO_REDO"):
        redo(h, raise_on_error=True)


# ===========================================================================
# 7. Undo Followed by New Edit (Branching clears Redo)
# ===========================================================================

def test_undo_followed_by_new_edit():
    words = make_test_words()
    h = create_history(make_test_state(), now=100)

    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=0, end=1000)]}], words, "Cut 1")["history"]
    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=1000, end=2000)]}], words, "Cut 2")["history"]

    # Undo to r1
    h, _ = undo(h)
    assert head(h).id == "r1"
    assert h.redo == ["r2"]

    # Apply a NEW edit at r1: Speed up 3000-5000
    speed_op = [{"type": "speed", "range": Interval(start=3000, end=5000), "speed": 2.0}]
    res = apply_and_commit(h, speed_op, words, "Speed new branch", now=500)
    assert res["ok"] is True
    h = res["history"]
    r3 = res["revision"]

    # Revision r3 created with parent r1
    assert r3.id == "r3"
    assert r3.parentId == "r1"
    assert head(h).id == "r3"

    # Redo branch MUST be cleared
    assert h.redo == []
    assert not can_redo(h)
    assert h.undo == ["r0", "r1", "r3"]

    # Total revisions list contains r0, r1, r2, r3
    assert len(h.revisions) == 4
    assert [r.id for r in h.revisions] == ["r0", "r1", "r2", "r3"]


# ===========================================================================
# 8. Restore an Earlier Revision
# ===========================================================================

def test_restore_earlier_revision():
    words = make_test_words()
    h = create_history(make_test_state(), now=100)

    # r1: cut 0-2000
    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=0, end=2000)]}], words, "Cut 1")["history"]
    r1_hash = head(h).hash

    # r2: speed 4000-6000 at 2x
    h = apply_and_commit(h, [{"type": "speed", "range": Interval(start=4000, end=6000), "speed": 2.0}], words, "Speed")["history"]

    # r3: add caption
    h = apply_and_commit(h, [{"type": "add_caption", "text": "Intro", "wordIds": ["w3"]}], words, "Cap")["history"]

    # Restore r1
    h, r4 = restore_revision(h, "r1", now=600)
    assert r4.id == "r4"
    assert r4.kind == "restore"
    assert r4.parentId == "r3"
    assert r4.label == "Restore r1"
    assert r4.hash == r1_hash
    assert head(h).id == "r4"
    assert h.undo == ["r0", "r1", "r2", "r3", "r4"]

    # Restored state has 8000ms duration (matching r1), no speed change, no caption
    assert timeline_duration(r4.state.segments) == 8000.0
    assert r4.state.captions == []

    # Undo on restore returns to r3!
    h, undone = undo(h)
    assert undone.id == "r4"
    assert head(h).id == "r3"
    assert len(head(h).state.captions) == 1

    # Restoring non-existent revision raises error
    with pytest.raises(ValueError, match="UNKNOWN_REVISION"):
        restore_revision(h, "r99")

    # Restoring current head raises NO_CHANGE
    with pytest.raises(ValueError, match="NO_CHANGE"):
        restore_revision(h, "r3")


# ===========================================================================
# 9. Failed Operation Does NOT Create a Revision
# ===========================================================================

def test_failed_operation_does_not_create_revision():
    words = make_test_words()
    h = create_history(make_test_state(duration=10_000), now=100)
    initial_rev_count = len(h.revisions)

    # 1. Invalid range (out of bounds)
    res_fail1 = apply_and_commit(
        h,
        [{"type": "cut", "ranges": [Interval(start=0, end=25_000)]}],
        words,
        "Bad Cut",
    )
    assert res_fail1["ok"] is False
    assert res_fail1["error"].code == "RANGE_OUT_OF_BOUNDS"
    assert len(res_fail1["history"].revisions) == initial_rev_count
    assert res_fail1["history"].undo == ["r0"]
    assert head(res_fail1["history"]).id == "r0"

    # 2. Whole video cut
    res_fail2 = apply_and_commit(
        h,
        [{"type": "cut", "ranges": [Interval(start=0, end=10_000)]}],
        words,
        "Cut All",
    )
    assert res_fail2["ok"] is False
    assert res_fail2["error"].code == "WHOLE_VIDEO_CUT"
    assert len(res_fail2["history"].revisions) == initial_rev_count

    # 3. Successful cut creates r1
    res_ok = apply_and_commit(
        h,
        [{"type": "cut", "ranges": [Interval(start=0, end=2000)]}],
        words,
        "Good Cut",
    )
    assert res_ok["ok"] is True
    h = res_ok["history"]
    assert head(h).id == "r1"
    assert len(h.revisions) == 2

    # 4. Duplicate cut produces NO_CHANGE and does NOT create r2
    res_noop = apply_and_commit(
        h,
        [{"type": "cut", "ranges": [Interval(start=0, end=2000)]}],
        words,
        "Duplicate Cut",
    )
    assert res_noop["ok"] is False
    assert res_noop["error"].code == "NO_CHANGE"
    assert len(res_noop["history"].revisions) == 2
    assert head(res_noop["history"]).id == "r1"


# ===========================================================================
# 10. Cuts and Keep Operations with Undo / Redo
# ===========================================================================

def test_cuts_and_keep_operations_with_undo_redo():
    words = make_test_words()
    h = create_history(make_test_state(duration=10_000))

    # Cut 2000-4000
    h = apply_and_commit(h, [{"type": "cut", "ranges": [Interval(start=2000, end=4000)]}], words, "Cut")["history"]
    assert len(head(h).state.segments) == 2
    assert timeline_duration(head(h).state.segments) == 8000.0

    # Keep only 0-1000 and 5000-8000
    h = apply_and_commit(
        h,
        [{"type": "keep", "ranges": [Interval(start=0, end=1000), Interval(start=5000, end=8000)]}],
        words,
        "Keep specific parts",
    )["history"]
    assert len(head(h).state.segments) == 2
    assert timeline_duration(head(h).state.segments) == 4000.0

    # Undo Keep -> back to Cut state
    h, _ = undo(h)
    assert timeline_duration(head(h).state.segments) == 8000.0

    # Undo Cut -> back to whole video
    h, _ = undo(h)
    assert timeline_duration(head(h).state.segments) == 10_000.0

    # Redo Cut -> 8000ms
    h, _ = redo(h)
    assert timeline_duration(head(h).state.segments) == 8000.0

    # Redo Keep -> 4000ms
    h, _ = redo(h)
    assert timeline_duration(head(h).state.segments) == 4000.0


# ===========================================================================
# 11. Speed Changes with Undo / Redo
# ===========================================================================

def test_speed_changes_with_undo_redo():
    words = make_test_words()
    h = create_history(make_test_state(duration=10_000))

    # Speed 2.0x on 0-4000
    h = apply_and_commit(
        h,
        [{"type": "speed", "range": Interval(start=0, end=4000), "speed": 2.0}],
        words,
        "2x first 4s",
    )["history"]
    # 4000 / 2 + 6000 = 8000
    assert timeline_duration(head(h).state.segments) == 8000.0

    # Speed 0.5x on 4000-6000
    h = apply_and_commit(
        h,
        [{"type": "speed", "range": Interval(start=4000, end=6000), "speed": 0.5}],
        words,
        "0.5x 4-6s",
    )["history"]
    # 2000 + 4000 + 4000 = 10000
    assert timeline_duration(head(h).state.segments) == 10_000.0

    # Undo -> back to 8000
    h, _ = undo(h)
    assert timeline_duration(head(h).state.segments) == 8000.0

    # Undo -> back to original 10000 at 1x
    h, _ = undo(h)
    assert head(h).state.segments[0].speed == 1.0

    # Redo both
    h, _ = redo(h)
    assert timeline_duration(head(h).state.segments) == 8000.0
    h, _ = redo(h)
    assert timeline_duration(head(h).state.segments) == 10_000.0


# ===========================================================================
# 12. Reordering with Undo / Redo
# ===========================================================================

def test_reordering_with_undo_redo():
    words = make_test_words()
    # 2 segments: 0-5000 and 5000-10000
    s0 = make_test_state(
        segments=[
            Segment(srcStart=0, srcEnd=5000, speed=1.0),
            Segment(srcStart=5000, srcEnd=10000, speed=1.0),
        ]
    )
    h = create_history(s0)

    # Move 5000-10000 to "start"
    h = apply_and_commit(
        h,
        [{"type": "move", "range": Interval(start=5000, end=10000), "to": "start"}],
        words,
        "Move second half to start",
    )["history"]

    assert head(h).state.segments[0].srcStart == 5000
    assert head(h).state.segments[1].srcStart == 0

    # Undo -> original order
    h, _ = undo(h)
    assert head(h).state.segments[0].srcStart == 0
    assert head(h).state.segments[1].srcStart == 5000

    # Redo -> reordered
    h, _ = redo(h)
    assert head(h).state.segments[0].srcStart == 5000
    assert head(h).state.segments[1].srcStart == 0


# ===========================================================================
# 13. Captions and Overlays with Undo / Redo
# ===========================================================================

def test_captions_and_overlays_with_undo_redo():
    words = make_test_words()
    h = create_history(make_test_state(duration=10_000))

    # 1. Add Caption
    h = apply_and_commit(
        h,
        [{"type": "add_caption", "text": "Hello caption", "wordIds": ["w1", "w2"]}],
        words,
        "Add Caption",
    )["history"]
    assert len(head(h).state.captions) == 1
    assert head(h).state.captions[0].text == "Hello caption"

    # 2. Add Source-Anchored Overlay
    h = apply_and_commit(
        h,
        [
            {
                "type": "add_text_overlay",
                "text": "Source banner",
                "anchorType": "source",
                "startMs": 1000,
                "endMs": 3000,
                "position": "top",
                "style": {"color": "#ff0000", "fontSize": "24px"},
            }
        ],
        words,
        "Add Source Overlay",
    )["history"]
    assert len(head(h).state.overlays) == 1
    assert head(h).state.overlays[0].anchorType == "source"

    # 3. Add Timeline-Anchored Overlay
    h = apply_and_commit(
        h,
        [
            {
                "type": "add_text_overlay",
                "text": "Timeline banner",
                "anchorType": "timeline",
                "startMs": 0,
                "endMs": 4000,
                "position": "bottom",
            }
        ],
        words,
        "Add Timeline Overlay",
    )["history"]
    assert len(head(h).state.overlays) == 2

    # Undo #3 -> 1 overlay
    h, _ = undo(h)
    assert len(head(h).state.overlays) == 1
    assert head(h).state.overlays[0].text == "Source banner"

    # Undo #2 -> 0 overlays, 1 caption
    h, _ = undo(h)
    assert len(head(h).state.overlays) == 0
    assert len(head(h).state.captions) == 1

    # Undo #1 -> 0 captions
    h, _ = undo(h)
    assert len(head(h).state.captions) == 0

    # Redo all three
    h, _ = redo(h)
    assert len(head(h).state.captions) == 1
    h, _ = redo(h)
    assert len(head(h).state.overlays) == 1
    h, _ = redo(h)
    assert len(head(h).state.overlays) == 2
    assert head(h).state.overlays[0].style == {"color": "#ff0000", "fontSize": "24px"}


# ===========================================================================
# 14. Snapshot Isolation and Nested Mutable Data
# ===========================================================================

def test_snapshot_isolation_and_nested_mutable_data():
    words = make_test_words()
    s0 = make_test_state(
        duration=10_000,
        captions=[
            Caption(
                id="cap_1",
                text="Original",
                wordIds=["w1", "w2"],
                size="md",
                position="bottom",
            )
        ],
        effects=[
            AudioEffect(
                id="fx_1",
                kind="mute",
                wordId="w1",
                srcStart=500,
                srcEnd=1500,
            )
        ],
        overlays=[
            TextOverlay(
                id="ov_1",
                text="Title",
                anchorType="source",
                startMs=0,
                endMs=2000,
                position="top",
                style={"font": "Arial", "color": "blue"},
            )
        ],
        next_id=2,
    )

    h = create_history(s0, now=100)
    r0 = h.revisions[0]
    r0_hash = r0.hash

    # Mutate the original s0 after creating history
    s0.segments.append(Segment(srcStart=10000, srcEnd=20000, speed=1.0))
    s0.captions[0].wordIds.append("w_hacked")
    s0.overlays[0].style["color"] = "hacked_red"
    s0.nextId = 9999

    # Verify r0 snapshot was NOT affected by mutations to s0
    assert len(r0.state.segments) == 1
    assert r0.state.captions[0].wordIds == ["w1", "w2"]
    assert r0.state.overlays[0].style == {"font": "Arial", "color": "blue"}
    assert r0.state.nextId == 2
    assert r0.hash == r0_hash

    # Commit a new revision
    h = apply_and_commit(
        h,
        [{"type": "cut", "ranges": [Interval(start=4000, end=6000)]}],
        words,
        "Cut middle",
        now=200,
    )["history"]
    r1 = head(h)
    r1_hash = r1.hash

    # Mutate the state object returned by current_state(h)
    exposed_state = current_state(h)
    exposed_state.segments.clear()
    exposed_state.captions.clear()
    exposed_state.overlays[0].style["font"] = "Comic Sans"

    # Verify r1 snapshot is completely isolated and intact
    assert len(r1.state.segments) == 2
    assert len(r1.state.captions) == 1
    assert r1.state.overlays[0].style["font"] == "Arial"
    assert r1.hash == r1_hash


# ===========================================================================
# 15. RevisionManager Stateful Controller
# ===========================================================================

def test_revision_manager_api():
    words = make_test_words()
    s0 = initial_state(10_000)
    mgr = RevisionManager(
        s0,
        source_video_path="/path/to/source.mp4",
        source_hash="sha256_mock_source",
        now=1000,
        label="Project Start",
    )

    assert mgr.source_video_path == "/path/to/source.mp4"
    assert mgr.source_hash == "sha256_mock_source"
    assert mgr.current_revision.id == "r0"
    assert not mgr.can_undo
    assert not mgr.can_redo

    # 1. Successful edit
    res1 = mgr.apply(
        [{"type": "cut", "ranges": [Interval(start=0, end=3000)]}],
        words,
        "Cut 3s",
        now=1050,
    )
    assert res1["ok"] is True
    assert mgr.current_revision.id == "r1"
    assert mgr.can_undo
    assert not mgr.can_redo
    assert len(mgr.revisions) == 2

    # 2. Failed edit (invalid range) -> no revision created
    res_fail = mgr.apply(
        [{"type": "cut", "ranges": [Interval(start=5000, end=2000)]}],
        words,
        "Bad range",
    )
    assert res_fail["ok"] is False
    assert mgr.current_revision.id == "r1"
    assert len(mgr.revisions) == 2

    # 3. Another successful edit
    res2 = mgr.apply(
        [{"type": "add_caption", "text": "Subtitle", "wordIds": ["w3"]}],
        words,
        "Caption",
        now=1100,
    )
    assert res2["ok"] is True
    assert mgr.current_revision.id == "r2"

    # 4. Undo
    undone = mgr.undo()
    assert undone.id == "r2"
    assert mgr.current_revision.id == "r1"
    assert mgr.can_redo

    # 5. Redo
    redone = mgr.redo()
    assert redone.id == "r2"
    assert mgr.current_revision.id == "r2"

    # 6. Restore r0
    restored = mgr.restore("r0", now=1200)
    assert restored.id == "r3"
    assert restored.kind == "restore"
    assert mgr.current_revision.id == "r3"
    assert timeline_duration(mgr.current_state.segments) == 10_000.0
