"""Tests for render_plan and caption_mapper modules."""

from __future__ import annotations

import pytest

from backend.app.engine.types import (
    Caption,
    Interval,
    Segment,
    TextOverlay,
    TimelineState,
    Transcript,
    Word,
)
from backend.app.media.caption_mapper import (
    MIN_CAPTION_MS,
    resolve_captions,
)
from backend.app.media.render_plan import (
    atempo_chain,
    build_render_plan,
    CaptionOverlay,
    FilterStep,
    RenderPlan,
)


def _make_sample_transcript() -> Transcript:
    """Create a sample transcript with 5 words spanning 0 to 5000 ms."""
    words = [
        Word(id="w1", index=0, text="Welcome", start=0, end=800),
        Word(id="w2", index=1, text="to", start=900, end=1200),
        Word(id="w3", index=2, text="the", start=1300, end=1600),
        Word(id="w4", index=3, text="studio", start=1700, end=2600),
        Word(id="w5", index=4, text="today", start=3000, end=3800),
    ]
    return Transcript(
        provider="whisper",
        model="base",
        note=None,
        durationMs=5000,
        speakers={"spk1": {"label": "Host"}},
        words=words,
    )


def test_caption_mapping_all_words_kept():
    transcript = _make_sample_transcript()
    words_by_id = {w.id: w for w in transcript.words}

    cap = Caption(
        id="c1",
        text="Welcome to the studio",
        wordIds=["w1", "w2", "w3", "w4"],
        size="md",
        position="bottom",
    )
    state = TimelineState(
        sourceDurationMs=5000,
        segments=[Segment(srcStart=0, srcEnd=5000, speed=1.0)],
        captions=[cap],
    )

    resolved = resolve_captions(state, words_by_id)
    assert len(resolved) == 1
    assert resolved[0].status == "visible"
    assert resolved[0].kept_words == 4
    assert resolved[0].total_words == 4
    assert len(resolved[0].spans) == 1
    span = resolved[0].spans[0]
    assert span.tlStart == 0
    assert span.tlEnd == 2600


def test_caption_mapping_fully_removed():
    transcript = _make_sample_transcript()
    words_by_id = {w.id: w for w in transcript.words}

    cap = Caption(
        id="c1",
        text="Welcome to the studio",
        wordIds=["w1", "w2", "w3", "w4"],
        size="md",
        position="bottom",
    )
    # Cut 0 to 3000 ms completely, keep only w5 (3000-5000)
    state = TimelineState(
        sourceDurationMs=5000,
        segments=[Segment(srcStart=3000, srcEnd=5000, speed=1.0)],
        captions=[cap],
    )

    resolved = resolve_captions(state, words_by_id)
    assert len(resolved) == 1
    assert resolved[0].status == "removed"
    assert resolved[0].kept_words == 0
    assert len(resolved[0].spans) == 0


def test_caption_mapping_partial_retention():
    transcript = _make_sample_transcript()
    words_by_id = {w.id: w for w in transcript.words}

    cap = Caption(
        id="c1",
        text="Welcome to the studio",
        wordIds=["w1", "w2", "w3", "w4"],
        size="md",
        position="bottom",
    )
    # Cut w1 and w2 (0-1200), keep w3 and w4 (1300-5000)
    state = TimelineState(
        sourceDurationMs=5000,
        segments=[Segment(srcStart=1300, srcEnd=5000, speed=1.0)],
        captions=[cap],
    )

    resolved = resolve_captions(state, words_by_id)
    assert len(resolved) == 1
    assert resolved[0].status == "partial"
    assert resolved[0].kept_words == 2
    assert resolved[0].total_words == 4
    assert len(resolved[0].spans) == 1
    # On timeline, w3 starts at 0 (since 1300 is segment start), w4 ends at 1300
    span = resolved[0].spans[0]
    assert span.tlStart == 0
    assert span.tlEnd == 1300


def test_caption_mapping_min_duration_700ms_sped_up():
    """Verify that a short caption or sped-up content enforces 700 ms minimum."""
    w = Word(id="w1", index=0, text="Quick", start=0, end=400)
    transcript = Transcript(
        provider="whisper",
        model="base",
        note=None,
        durationMs=2000,
        speakers={},
        words=[w],
    )
    words_by_id = {"w1": w}
    cap = Caption(id="c1", text="Quick", wordIds=["w1"], size="sm", position="bottom")

    # Sped up at 2.0x -> raw duration is 400 / 2.0 = 200 ms
    state = TimelineState(
        sourceDurationMs=2000,
        segments=[Segment(srcStart=0, srcEnd=2000, speed=2.0)],
        captions=[cap],
    )

    resolved = resolve_captions(state, words_by_id)
    assert len(resolved) == 1
    assert len(resolved[0].spans) == 1
    span = resolved[0].spans[0]
    # Raw timeline end is 200, but minimum duration extends it to 700 ms
    assert span.tlStart == 0
    assert span.tlEnd == 700
    assert span.extended is True
    assert span.minReached is True


def test_caption_mapping_reordering_disjoint_spans():
    """Verify that reordered words separated by cut/gap create separate spans."""
    w1 = Word(id="w1", index=0, text="PartOne", start=0, end=500)
    w2 = Word(id="w2", index=1, text="PartTwo", start=3000, end=3500)
    transcript = Transcript(
        provider="whisper",
        model="base",
        note=None,
        durationMs=5000,
        speakers={},
        words=[w1, w2],
    )
    words_by_id = {"w1": w1, "w2": w2}
    cap = Caption(id="c1", text="Two Parts", wordIds=["w1", "w2"], size="lg", position="top")

    # Segment 2 (3000-4000) placed first, then Segment 1 (0-1000)
    # With a 1000 ms gap between them
    state = TimelineState(
        sourceDurationMs=5000,
        segments=[
            Segment(srcStart=3000, srcEnd=4000, speed=1.0),
            Segment(srcStart=0, srcEnd=1000, speed=1.0),
        ],
        captions=[cap],
    )

    resolved = resolve_captions(state, words_by_id)
    assert len(resolved) == 1
    # Words are in separate timeline segments separated by > 400 ms -> 2 spans
    assert len(resolved[0].spans) == 2


def test_atempo_chain():
    assert atempo_chain(1.0) == ""
    assert atempo_chain(1.5) == "atempo=1.500000"
    assert atempo_chain(2.0) == "atempo=2.000000"
    assert atempo_chain(4.0) == "atempo=2.0,atempo=2.000000"
    assert atempo_chain(0.5) == "atempo=0.500000"
    assert atempo_chain(0.25) == "atempo=0.5,atempo=0.500000"


def test_build_render_plan():
    transcript = _make_sample_transcript()
    cap = Caption(id="c1", text="Welcome", wordIds=["w1"], size="lg", position="top")
    overlay = TextOverlay(
        id="ov1",
        text="Watermark",
        anchorType="timeline",
        startMs=0,
        endMs=3000,
        position="bottom",
    )

    state = TimelineState(
        sourceDurationMs=5000,
        segments=[
            Segment(srcStart=0, srcEnd=2000, speed=1.0),
            Segment(srcStart=3000, srcEnd=5000, speed=2.0),
        ],
        captions=[cap],
        overlays=[overlay],
    )

    plan = build_render_plan(state, transcript, has_video=True, has_audio=True)
    assert len(plan.filter_steps) == 2
    assert "trim=start=0.000:end=2.000" in plan.filter_steps[0].video_filter
    assert "atrim=start=0.000:end=2.000" in plan.filter_steps[0].audio_filter
    assert "trim=start=3.000:end=5.000" in plan.filter_steps[1].video_filter
    assert "atempo=2.000000" in plan.filter_steps[1].audio_filter

    assert len(plan.captions) == 1
    assert plan.captions[0].text == "Welcome"
    assert plan.captions[0].y == "40"  # top position
    assert plan.captions[0].font_size == 40  # lg size

    assert len(plan.overlays) == 1
    assert plan.overlays[0].text == "Watermark"
    assert plan.overlays[0].start_ms == 0
    assert plan.overlays[0].end_ms == 3000

    # Total duration = (2000 - 0) / 1.0 + (5000 - 3000) / 2.0 = 2000 + 1000 = 3000 ms
    assert plan.expected_duration_ms == 3000
    assert plan.timeline_hash is not None
