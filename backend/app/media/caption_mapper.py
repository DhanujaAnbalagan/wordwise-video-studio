"""Caption and text overlay mapping for the VideoFlow AI rendering pipeline.

This module maps captions and text overlays from source-time / word-anchored
representations to final timeline-time display intervals.

Rules:
1. Captions are anchored to transcript word IDs.
2. Words whose surviving duration is < 50% of original are considered cut.
3. Fully removed captions (all anchored words cut) have status "removed" and are omitted from render output.
4. Partially retained captions have status "partial"; they are displayed over the surviving words, text unchanged.
5. Surviving words that are not adjacent in the timeline (separated by > 400ms after cuts or reordering)
   form separate spans.
6. Minimum caption display duration is 700 ms (MIN_CAPTION_MS = 700) where feasible, extended up to
   the next caption span start or total timeline duration.
7. TextOverlays with anchorType="source" follow source segments and cuts; anchorType="timeline"
   are placed directly on the timeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Union

from ..engine.types import Caption, Ms, Segment, TextOverlay, TimelineState, Transcript, Word
from ..engine.timeline import layout, place_word, PlacedSegment, WORD_KEEP_FRACTION

MIN_CAPTION_MS: int = 700
MAX_WORD_GAP_MS: int = 400


@dataclass
class CaptionSpan:
    """A continuous timeline interval for displaying a caption."""
    tlStart: float
    tlEnd: float
    extended: bool = False
    minReached: bool = True


@dataclass
class ResolvedCaption:
    """A caption resolved against the edited timeline."""
    caption: Caption
    status: Literal["visible", "partial", "removed"]
    kept_words: int
    total_words: int
    spans: List[CaptionSpan] = field(default_factory=list)


def resolve_captions(
    state: TimelineState,
    words_by_id: Dict[str, Word],
    placed: Optional[List[PlacedSegment]] = None,
) -> List[ResolvedCaption]:
    """Resolve state.captions against the placed timeline segments.

    Deterministic implementation matching the TypeScript engine specification.
    """
    if placed is None:
        placed = layout(state.segments)
    total_tl = placed[-1].tlEnd if placed else 0.0

    resolved_list: List[ResolvedCaption] = []

    for caption in state.captions:
        words = [words_by_id[wid] for wid in caption.wordIds if wid in words_by_id]
        if not words:
            resolved_list.append(
                ResolvedCaption(
                    caption=caption,
                    status="removed",
                    kept_words=0,
                    total_words=len(caption.wordIds),
                    spans=[],
                )
            )
            continue

        # Place words on timeline
        visible_words = []
        for w in words:
            pw = place_word(placed, w)
            if pw["visible"] and pw["tlStart"] is not None and pw["tlEnd"] is not None:
                visible_words.append((w, pw["tlStart"], pw["tlEnd"]))

        # Sort visible words by timeline start
        visible_words.sort(key=lambda item: item[1])

        if not visible_words:
            resolved_list.append(
                ResolvedCaption(
                    caption=caption,
                    status="removed",
                    kept_words=0,
                    total_words=len(words),
                    spans=[],
                )
            )
            continue

        status: Literal["visible", "partial"] = (
            "visible" if len(visible_words) == len(words) else "partial"
        )

        # Group words into spans (words separated by <= 400 ms are merged)
        spans: List[CaptionSpan] = []
        for _, w_start, w_end in visible_words:
            if spans and (w_start - spans[-1].tlEnd) <= MAX_WORD_GAP_MS:
                spans[-1].tlEnd = max(spans[-1].tlEnd, w_end)
            else:
                spans.append(CaptionSpan(tlStart=w_start, tlEnd=w_end, extended=False, minReached=True))

        resolved_list.append(
            ResolvedCaption(
                caption=caption,
                status=status,
                kept_words=len(visible_words),
                total_words=len(words),
                spans=spans,
            )
        )

    # Apply minimum caption duration (700 ms) across all spans
    all_spans: List[CaptionSpan] = [s for rc in resolved_list for s in rc.spans]
    all_spans.sort(key=lambda s: s.tlStart)

    for i, s in enumerate(all_spans):
        cur_dur = s.tlEnd - s.tlStart
        if cur_dur >= MIN_CAPTION_MS:
            continue
        limit = all_spans[i + 1].tlStart if i + 1 < len(all_spans) else total_tl
        limit = min(limit, total_tl)
        target = s.tlStart + MIN_CAPTION_MS
        new_end = max(s.tlEnd, min(target, limit))
        s.extended = new_end > s.tlEnd
        s.minReached = (new_end >= target - 0.5)
        s.tlEnd = new_end

    return resolved_list
