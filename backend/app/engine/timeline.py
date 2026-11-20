"""Timeline utilities for the VideoFlow AI editing engine.

This module mirrors the behavior of `src/engine/timeline.ts` using pure Python.
It operates on the data structures declared in `engine/types.py`.
All functions are immutable – they never modify their inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict

from .types import Segment, Ms, Interval


@dataclass(frozen=True)
class PlacedSegment:
    """A segment with its calculated timeline offsets.

    Attributes
    ----------
    srcStart: Ms – start in source time.
    srcEnd: Ms – end in source time.
    speed: float – playback speed for this segment.
    tlStart: Ms – start offset in the edited timeline.
    tlEnd: Ms – end offset in the edited timeline.
    """

    srcStart: Ms
    srcEnd: Ms
    speed: float
    tlStart: Ms
    tlEnd: Ms


def layout(segs: List[Segment]) -> List[PlacedSegment]:
    """Calculate the timeline placement for a list of source segments.

    The algorithm follows the TypeScript implementation:
    * Segments are processed in the order given.
    * Each segment contributes an edited duration of ``(srcEnd - srcStart) / speed``.
    * ``tlStart`` is the cumulative edited duration of all previous segments.
    * ``tlEnd`` = ``tlStart`` + edited duration.
    """
    placed: List[PlacedSegment] = []
    tl_cursor: Ms = 0
    for s in segs:
        # Guard against zero‑length or inverted intervals – they are ignored in TS.
        if s.srcEnd <= s.srcStart:
            continue
        edited_len = int((s.srcEnd - s.srcStart) / s.speed)
        placed.append(
            PlacedSegment(
                srcStart=s.srcStart,
                srcEnd=s.srcEnd,
                speed=s.speed,
                tlStart=tl_cursor,
                tlEnd=tl_cursor + edited_len,
            )
        )
        tl_cursor += edited_len
    return placed


def src_to_tl(placed: List[PlacedSegment], t: Ms) -> Optional[Ms]:
    """Map a source‑time ``t`` to its timeline position.

    Returns ``None`` when the source timestamp falls inside a cut (i.e. no segment covers it).
    If ``t`` exactly matches the end of a segment, the corresponding ``tlEnd`` is returned.
    """
    for seg in placed:
        if t < seg.srcStart:
            continue
        if seg.srcStart <= t < seg.srcEnd:
            # Inside the segment – linear mapping using the segment speed.
            offset = int((t - seg.srcStart) / seg.speed)
            return seg.tlStart + offset
        if t == seg.srcEnd:
            return seg.tlEnd
    return None


def tl_to_src(placed: List[PlacedSegment], x: Ms) -> Optional[Dict[str, int]]:
    """Map a timeline time ``x`` back to source time.

    The function clamps ``x`` to the total timeline length – values beyond the last
    ``tlEnd`` resolve to the final source end.
    Returns a dict with ``src`` (source ms) and ``segIndex`` (the index of the
    segment that produced the mapping).
    """
    if not placed:
        return None
    total_tl = placed[-1].tlEnd
    if x >= total_tl:
        last = placed[-1]
        return {"src": last.srcEnd, "segIndex": len(placed) - 1}
    if x < 0:
        x = 0
    for idx, seg in enumerate(placed):
        if seg.tlStart <= x < seg.tlEnd:
            src_offset = int((x - seg.tlStart) * seg.speed)
            return {"src": seg.srcStart + src_offset, "segIndex": idx}
    return None


def _merge_intervals(intervals: List[Interval]) -> List[Interval]:
    """Merge overlapping or adjacent intervals and return a sorted list.
    """
    if not intervals:
        return []
    # Sort by start time.
    sorted_ints = sorted(intervals, key=lambda i: i.start)
    merged: List[Interval] = []
    cur = sorted_ints[0]
    for nxt in sorted_ints[1:]:
        if nxt.start <= cur.end:  # overlap or touching
            cur = Interval(start=cur.start, end=max(cur.end, nxt.end))
        else:
            merged.append(cur)
            cur = nxt
    merged.append(cur)
    return merged


def present_intervals(segs: List[Segment]) -> List[Interval]:
    """Return the union of source intervals covered by the provided segments.

    The TypeScript version uses ``mergeIntervals`` on the raw ``srcStart``/``srcEnd``
    pairs of each segment, discarding zero‑length entries.
    """
    raw: List[Interval] = []
    for s in segs:
        if s.srcEnd > s.srcStart:
            raw.append(Interval(start=s.srcStart, end=s.srcEnd))
    return _merge_intervals(raw)
