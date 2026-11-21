"""Timeline utilities for the VideoFlow AI editing engine.

This module mirrors the behavior of `src/engine/timeline.ts` using pure Python.
All functions are immutable – they never modify their inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict

from .types import Segment, Ms, Interval, Word

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Core layout helpers (already present in the file, kept unchanged)
# ---------------------------------------------------------------------------

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
        edited_len = (s.srcEnd - s.srcStart) / s.speed
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
            offset = (t - seg.srcStart) / seg.speed
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
            src_offset = (x - seg.tlStart) * seg.speed
            return {"src": seg.srcStart + src_offset, "segIndex": idx}
    return None

# ---------------------------------------------------------------------------
# Helper utilities used by operations (newly added)
# ---------------------------------------------------------------------------

def _merge_intervals(intervals: List[Interval]) -> List[Interval]:
    """Merge overlapping or adjacent intervals and return a sorted list."""
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


def overlap(a: Interval, b: Interval) -> Ms:
    """Return the overlapping length of two intervals (non‑negative)."""
    return max(0, min(a.end, b.end) - max(a.start, b.start))

def present_ms(segs: List[Segment], r: Interval) -> Ms:
    """Total source‑ms of ``segs`` that intersect ``r``.

    Mirrors TS ``presentMs`` which sums ``overlap(p, r)`` for each merged interval.
    """
    total: Ms = 0
    for p in present_intervals(segs):
        total += overlap(p, r)
    return total

def split_at(segs: List[Segment], t: Ms) -> List[Segment]:
    """Split any segment that straddles ``t`` into two pieces.

    Equivalent to TS ``splitAt``.
    """
    out: List[Segment] = []
    for s in segs:
        if s.srcStart < t < s.srcEnd:
            out.append(Segment(srcStart=s.srcStart, srcEnd=t, speed=s.speed))
            out.append(Segment(srcStart=t, srcEnd=s.srcEnd, speed=s.speed))
        else:
            out.append(s)
    return out


def subtract_range(segs: List[Segment], r: Interval) -> List[Segment]:
    """Remove ``r`` from ``segs`` (cut operation).

    Mirrors TS ``subtractRange`` – segments wholly outside ``r`` are kept unchanged;
    segments intersecting ``r`` are trimmed on either side.
    """
    out: List[Segment] = []
    for s in segs:
        if r.end <= s.srcStart or r.start >= s.srcEnd:
            out.append(s)
            continue
        if s.srcStart < r.start:
            out.append(Segment(srcStart=s.srcStart, srcEnd=r.start, speed=s.speed))
        if r.end < s.srcEnd:
            out.append(Segment(srcStart=r.end, srcEnd=s.srcEnd, speed=s.speed))
    return out


def normalize_segments(segs: List[Segment]) -> List[Segment]:
    """Canonicalise segment list: drop empty pieces and merge neighbours with same speed.

    Mirrors TS ``normalizeSegments``.
    """
    out: List[Segment] = []
    for s in segs:
        if s.srcEnd - s.srcStart < 1:
            continue
        prev = out[-1] if out else None
        if prev and prev.srcEnd == s.srcStart and prev.speed == s.speed:
            # Merge with previous
            out[-1] = Segment(srcStart=prev.srcStart, srcEnd=s.srcEnd, speed=s.speed)
        else:
            out.append(s)
    return out


def timeline_duration(segs: List[Segment]) -> Ms:
    """Total edited timeline duration (source span divided by speed)."""
    total: float = 0.0
    for s in segs:
        total += (s.srcEnd - s.srcStart) / s.speed
    return total


def interval_minus(r: Interval, lst: List[Interval]) -> List[Interval]:
    """Subtract a list of intervals from ``r``.

    Returns the pieces of ``r`` that are not covered by any interval in ``lst``.
    Mirrors TS ``intervalMinus``.
    """
    pieces: List[Interval] = [Interval(start=r.start, end=r.end)]
    for x in lst:
        new_pieces: List[Interval] = []
        for p in pieces:
            if x.end <= p.start or x.start >= p.end:
                new_pieces.append(p)
                continue
            if p.start < x.start:
                new_pieces.append(Interval(start=p.start, end=x.start))
            if x.end < p.end:
                new_pieces.append(Interval(start=x.end, end=p.end))
        pieces = new_pieces
    # Filter out zero‑length intervals
    return [p for p in pieces if p.end > p.start]

# Constant matching TS implementation for word‑keep threshold
WORD_KEEP_FRACTION = 0.5


def place_word(placed: List[PlacedSegment], w: Word) -> Dict[str, Optional[float]]:
    """Determine how a transcript word appears in the current timeline.

    Returns a dict compatible with the TS ``WordPlacement`` interface:
    ``visible``, ``keptFraction``, ``tlStart``, ``tlEnd``, ``speed``.
    """
    dur = max(1, w.end - w.start)
    kept = 0
    best: Optional[Dict[str, float]] = None
    for p in placed:
        s = max(w.start, p.srcStart)
        e = min(w.end, p.srcEnd)
        if e > s:
            kept += e - s
            if not best or (e - s) > (best["e"] - best["s"]):
                best = {"p": p, "s": s, "e": e}
    kept_fraction = kept / dur
    if not best:
        return {
            "visible": False,
            "keptFraction": 0.0,
            "tlStart": None,
            "tlEnd": None,
            "speed": None,
        }
    p = best["p"]
    s = best["s"]
    e = best["e"]
    return {
        "visible": kept_fraction >= WORD_KEEP_FRACTION,
        "keptFraction": kept_fraction,
        "tlStart": p.tlStart + (s - p.srcStart) / p.speed,
        "tlEnd": p.tlStart + (e - p.srcStart) / p.speed,
        "speed": p.speed,
    }

# ---------------------------------------------------------------------------
# End of file
# ---------------------------------------------------------------------------
