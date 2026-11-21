"""Comprehensive tests for the natural-language instruction interpreter.
Covers:
- Remove first 10 seconds
- Cut a specified range
- Keep a specified range
- Speed up a segment
- Reorder identifiable segments
- Add captions when transcript exists
- Ambiguous target
- Missing transcript
- Unsupported instruction
- Invalid speed
- Invalid time range
"""

import pytest

from backend.app.engine.interpreter import InterpretContext, interpret
from backend.app.engine.timeline import Interval, initial_state
from backend.app.engine.types import Transcript, Word


@pytest.fixture
def sample_transcript():
    # 60s transcript with identifiable introduction, machine learning section, conclusion
    words = [
        # Intro: 0s - 4s
        Word(id="w1", index=0, text="Welcome", start=500, end=1500, speaker="S1"),
        Word(id="w2", index=1, text="to", start=1600, end=1800, speaker="S1"),
        Word(id="w3", index=2, text="the", start=1900, end=2100, speaker="S1"),
        Word(id="w4", index=3, text="introduction.", start=2200, end=3500, speaker="S1"),
        # Section 1: 10s - 20s (machine learning)
        Word(id="w5", index=4, text="Now", start=10000, end=10500, speaker="S1"),
        Word(id="w6", index=5, text="we", start=10600, end=10800, speaker="S1"),
        Word(id="w7", index=6, text="discuss", start=10900, end=11400, speaker="S1"),
        Word(id="w8", index=7, text="machine", start=11500, end=12000, speaker="S1"),
        Word(id="w9", index=8, text="learning", start=12100, end=12800, speaker="S1"),
        Word(id="w10", index=9, text="models.", start=12900, end=13800, speaker="S1"),
        # Section 2: 30s - 45s (pricing)
        Word(id="w11", index=10, text="Our", start=30000, end=30400, speaker="S2"),
        Word(id="w12", index=11, text="pricing", start=30500, end=31200, speaker="S2"),
        Word(id="w13", index=12, text="starts", start=31300, end=31800, speaker="S2"),
        Word(id="w14", index=13, text="at", start=31900, end=32100, speaker="S2"),
        Word(id="w15", index=14, text="ten", start=32200, end=32500, speaker="S2"),
        Word(id="w16", index=15, text="dollars.", start=32600, end=33400, speaker="S2"),
        # Conclusion: 50s - 58s
        Word(id="w17", index=16, text="In", start=50000, end=50300, speaker="S1"),
        Word(id="w18", index=17, text="conclusion,", start=50400, end=51500, speaker="S1"),
        Word(id="w19", index=18, text="thank", start=51600, end=52000, speaker="S1"),
        Word(id="w20", index=19, text="you", start=52100, end=52400, speaker="S1"),
        Word(id="w21", index=20, text="all.", start=52500, end=53500, speaker="S1"),
    ]
    return Transcript(
        provider="whisper",
        model="base",
        note=None,
        durationMs=60000,
        speakers={"S1": {"label": "Host"}, "S2": {"label": "Guest"}},
        words=words,
    )


@pytest.fixture
def ambiguous_transcript():
    # Transcript with "confidential" appearing twice
    words = [
        Word(id="w1", index=0, text="This", start=1000, end=1400),
        Word(id="w2", index=1, text="is", start=1500, end=1700),
        Word(id="w3", index=2, text="confidential.", start=1800, end=2800),
        Word(id="w4", index=3, text="And", start=10000, end=10400),
        Word(id="w5", index=4, text="that", start=10500, end=10800),
        Word(id="w6", index=5, text="is", start=10900, end=11100),
        Word(id="w7", index=6, text="confidential", start=11200, end=12000),
        Word(id="w8", index=7, text="too.", start=12100, end=12800),
    ]
    return Transcript(
        provider="whisper",
        model="base",
        note=None,
        durationMs=60000,
        speakers={},
        words=words,
    )


def test_remove_first_10_seconds(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(60000))
    res = interpret("Remove the first 10 seconds", ctx)
    assert res["kind"] == "ops"
    assert len(res["ops"]) == 1
    op = res["ops"][0]
    assert op["type"] == "cut"
    assert op["ranges"][0].start == 0
    assert op["ranges"][0].end == 10000

    # Structured plan checks
    plan = res["plan"]
    assert plan.status == "APPLIED"
    assert len(plan.operations) == 1
    assert plan.operations[0].type == "cut"
    assert plan.operations[0].parameters["ranges"] == [{"start": 0, "end": 10000}]


def test_cut_specified_range(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(60000))
    res = interpret("Cut the section from 00:30 to 00:45", ctx)
    assert res["kind"] == "ops"
    assert len(res["ops"]) == 1
    op = res["ops"][0]
    assert op["type"] == "cut"
    # Snapped to words near 30s-45s
    assert op["ranges"][0].start >= 29000
    assert op["ranges"][0].end <= 46000


def test_keep_specified_range(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(60000))
    res = interpret("Keep the section from 00:10 to 00:20", ctx)
    assert res["kind"] == "ops"
    assert len(res["ops"]) == 1
    op = res["ops"][0]
    assert op["type"] == "keep"
    assert op["ranges"][0].start >= 9500
    assert op["ranges"][0].end <= 20500


def test_keep_unambiguous_phrase_topic(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(60000))
    res = interpret("Keep only the section where the speaker explains machine learning", ctx)
    assert res["kind"] == "ops"
    assert len(res["ops"]) == 1
    op = res["ops"][0]
    assert op["type"] == "keep"
    # Should encompass the machine learning sentence (words w5 to w10: 10000 - 13800)
    assert op["ranges"][0].start == 10000
    assert op["ranges"][0].end == 13800


def test_speed_up_segment(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(60000))
    res = interpret("Speed up the introduction to 1.5x", ctx)
    assert res["kind"] == "ops"
    assert len(res["ops"]) == 1
    op = res["ops"][0]
    assert op["type"] == "speed"
    assert op["speed"] == 1.5
    # Intro range starts at 500ms
    assert op["range"].start <= 500


def test_speed_up_range_after(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(120000))
    res = interpret("Make the section after 1:20 play at 2x speed", ctx)
    assert res["kind"] == "ops"
    assert len(res["ops"]) == 1
    op = res["ops"][0]
    assert op["type"] == "speed"
    assert op["speed"] == 2.0
    assert op["range"].start == 80000
    assert op["range"].end == 120000


def test_reorder_identifiable_segments(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(60000))
    res = interpret("Move the conclusion before the introduction", ctx)
    assert res["kind"] == "ops"
    assert len(res["ops"]) == 1
    op = res["ops"][0]
    assert op["type"] == "move"
    # Moving conclusion
    assert op["range"].start == 50000
    assert op["range"].end == 53500
    # To before introduction start
    assert op["to"] == {"beforeSrc": 500}


def test_add_captions_when_transcript_exists(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(60000))
    res = interpret("Add captions", ctx)
    assert res["kind"] == "ops"
    assert len(res["ops"]) > 0
    for op in res["ops"]:
        assert op["type"] == "add_caption"
        assert len(op["wordIds"]) > 0


def test_add_captions_missing_transcript():
    ctx = InterpretContext(transcript=None, state=initial_state(60000))
    res = interpret("Add captions", ctx)
    assert res["kind"] == "reject"
    assert res["code"] == "MISSING_TRANSCRIPT"
    assert "transcript is required" in res["message"].lower()


def test_ambiguous_target(ambiguous_transcript):
    ctx = InterpretContext(transcript=ambiguous_transcript, state=initial_state(60000))
    res = interpret("Cut the word confidential", ctx)
    assert res["kind"] == "clarify"
    assert "2 times" in res["question"]
    assert len(res["options"]) >= 2
    assert res["plan"].status == "NEEDS_CLARIFICATION"


def test_missing_transcript_for_phrase_cut():
    ctx = InterpretContext(transcript=None, state=initial_state(60000))
    res = interpret("Cut the part about machine learning", ctx)
    assert res["kind"] == "reject"
    assert res["code"] == "MISSING_TRANSCRIPT"


def test_unsupported_instruction(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(60000))
    res = interpret("Color grade the video to teal and orange", ctx)
    assert res["kind"] == "reject"
    assert res["code"] == "UNSUPPORTED_INSTRUCTION"
    assert res["plan"].status == "REJECTED"


def test_invalid_speed(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(60000))
    res = interpret("Speed up the introduction to 5x", ctx)
    assert res["kind"] == "reject"
    assert res["code"] == "INVALID_SPEED"

    res_slow = interpret("Speed up the introduction to 0.1x", ctx)
    assert res_slow["kind"] == "reject"
    assert res_slow["code"] == "INVALID_SPEED"


def test_invalid_time_range(sample_transcript):
    ctx = InterpretContext(transcript=sample_transcript, state=initial_state(60000))
    # Ends before start
    res = interpret("Cut the section from 00:45 to 00:30", ctx)
    assert res["kind"] == "reject"
    assert res["code"] == "INVALID_RANGE"

    # Start out of bounds
    res_oob = interpret("Cut the section from 2:00 to 3:00", ctx)
    assert res_oob["kind"] == "reject"
    assert res_oob["code"] == "RANGE_OUT_OF_BOUNDS"
