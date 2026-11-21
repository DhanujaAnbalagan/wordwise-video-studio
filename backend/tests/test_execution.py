"""Execution and revision integration tests.
Verifies:
- Successful instruction creates exactly one revision
- Multi-operation instruction creates one revision
- Failed execution creates no revision
- Clarification creates no revision
- Rejected instruction creates no revision
- NO_CHANGE creates no revision
- Undo restores previous state
- Redo restores edited state
- New edit after undo clears redo
"""

import pytest

from backend.app.engine.executor import (
    create_session,
    redo,
    run_instruction,
    undo,
)
from backend.app.engine.history import can_redo, can_undo, head
from backend.app.engine.timeline import timeline_duration
from backend.app.engine.types import Transcript, Word


@pytest.fixture
def session():
    words = [
        Word(id="w1", index=0, text="Intro", start=1000, end=3000),
        Word(id="w2", index=1, text="middle", start=15000, end=18000),
        Word(id="w3", index=2, text="outro.", start=50000, end=55000),
    ]
    transcript = Transcript(
        provider="test",
        model="base",
        note=None,
        durationMs=120000,
        speakers={},
        words=words,
    )
    return create_session(transcript, 120000)


def test_successful_instruction_creates_one_revision(session):
    assert len(session.history.revisions) == 1
    assert session.history.undo == ["r0"]

    s1 = run_instruction(session, "Remove the first 10 seconds")
    assert s1.log[-1]["status"] == "applied"
    assert s1.log[-1]["revisionId"] == "r1"
    assert len(s1.history.revisions) == 2
    assert s1.history.undo == ["r0", "r1"]
    assert s1.history.redo == []


def test_multi_operation_instruction_creates_exactly_one_revision(session):
    # Multi-part instruction: cut and speed
    assert len(session.history.revisions) == 1
    s1 = run_instruction(
        session,
        "Remove the first 10 seconds and make the section after 1:20 play at 2x speed",
    )
    assert s1.log[-1]["status"] == "applied"
    assert s1.log[-1]["revisionId"] == "r1"
    # Even though there are multiple operations, only ONE revision is committed
    assert len(s1.history.revisions) == 2
    assert s1.history.undo == ["r0", "r1"]
    assert len(s1.history.revisions[-1].ops) == 2


def test_failed_execution_creates_no_revision(session):
    # Cut whole video is rejected by the operations engine
    s1 = run_instruction(session, "Cut the section from 00:00 to 02:00")
    assert s1.log[-1]["status"] == "rejected"
    assert len(s1.history.revisions) == 1
    assert s1.history.undo == ["r0"]


def test_clarification_creates_no_revision(session):
    # Ambiguous phrase or silence threshold clarification
    s1 = run_instruction(session, "Remove silences")
    assert s1.log[-1]["status"] == "needs_clarification"
    assert "revisionId" not in s1.log[-1] or s1.log[-1]["revisionId"] is None
    assert len(s1.history.revisions) == 1
    assert s1.history.undo == ["r0"]


def test_rejected_instruction_creates_no_revision(session):
    s1 = run_instruction(session, "Make the video dramatic with cinematic grade")
    assert s1.log[-1]["status"] == "rejected"
    assert len(s1.history.revisions) == 1
    assert s1.history.undo == ["r0"]


def test_no_change_creates_no_revision(session):
    # Cut 0-10s
    s1 = run_instruction(session, "Remove the first 10 seconds")
    assert s1.log[-1]["status"] == "applied"
    assert len(s1.history.revisions) == 2

    # Try cutting the same range again
    s2 = run_instruction(s1, "Remove the first 10 seconds")
    assert s2.log[-1]["status"] == "rejected"
    assert s2.log[-1]["error"]["code"] == "NO_CHANGE"
    assert len(s2.history.revisions) == 2
    assert s2.history.undo == ["r0", "r1"]


def test_undo_restores_previous_state(session):
    dur0 = timeline_duration(head(session.history).state.segments)
    s1 = run_instruction(session, "Remove the first 10 seconds")
    dur1 = timeline_duration(head(s1.history).state.segments)
    assert dur1 < dur0

    # Undo
    s2 = run_instruction(s1, "undo")
    assert s2.log[-1]["status"] == "applied"
    assert s2.history.undo == ["r0"]
    assert s2.history.redo == ["r1"]
    dur_undo = timeline_duration(head(s2.history).state.segments)
    assert dur_undo == dur0


def test_redo_restores_edited_state(session):
    s1 = run_instruction(session, "Remove the first 10 seconds")
    dur1 = timeline_duration(head(s1.history).state.segments)

    # Undo then redo
    s2 = run_instruction(s1, "undo")
    assert s2.history.undo == ["r0"]

    s3 = run_instruction(s2, "redo")
    assert s3.log[-1]["status"] == "applied"
    assert s3.history.undo == ["r0", "r1"]
    assert s3.history.redo == []
    dur_redo = timeline_duration(head(s3.history).state.segments)
    assert dur_redo == dur1


def test_new_edit_after_undo_clears_redo(session):
    # r0 -> r1 (cut 10s)
    s1 = run_instruction(session, "Remove the first 10 seconds")
    assert s1.history.undo == ["r0", "r1"]

    # Undo back to r0
    s2 = run_instruction(s1, "undo")
    assert s2.history.undo == ["r0"]
    assert s2.history.redo == ["r1"]
    assert can_redo(s2.history)

    # New edit after undo: cut 20s-30s
    s3 = run_instruction(s2, "Cut the section from 00:20 to 00:30")
    assert s3.log[-1]["status"] == "applied"
    assert s3.history.undo == ["r0", "r2"]
    # Redo stack MUST be cleared
    assert s3.history.redo == []
    assert not can_redo(s3.history)
