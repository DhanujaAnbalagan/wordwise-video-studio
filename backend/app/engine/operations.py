"""
Deterministic editing operations for VideoFlow AI.

Mirrors ``src/engine/operations.ts`` as a pure-functional Python module:
the input ``TimelineState`` is **never** mutated – a fresh state object is
returned on success.  Errors are represented by the ``EngineError`` dataclass
defined in ``types.py``.

Supported operation types
--------------------------
cut           – remove one or more source-time ranges from the timeline.
restore       – bring back previously-removed source-time ranges.
speed         – change playback speed for a source-time range.
mute          – mute audio for specific transcript words.
bleep         – replace audio with a bleep for specific transcript words.
clear_audio   – remove mute/bleep effects from specific transcript words.
add_caption   – add a caption anchored to transcript word IDs.
remove_caption– delete an existing caption by ID.
style_caption – change the size or position of an existing caption.
move          – reorder a source-time range to a new position in the timeline.

NOT implemented here (out of scope for Phase 2):
  - Undo / redo
  - Instruction text parsing / AI interpretation
  - FFmpeg rendering
  - API endpoints / revision history
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import Dict, List, Optional, Tuple, TypedDict, Union

from .types import (
    AudioEffect,
    Caption,
    EngineError,
    Interval,
    Op,
    Segment,
    TimelineState,
    Word,
    CutOp,
    RestoreOp,
    SpeedOp,
    AudioOp,
    AddCaptionOp,
    RemoveCaptionOp,
    StyleCaptionOp,
    MoveOp,
)
from .timeline import (
    interval_minus,
    layout,
    normalize_segments,
    place_word,
    present_intervals,
    present_ms,
    split_at,
    subtract_range,
    timeline_duration,
)

# ---------------------------------------------------------------------------
# Public constants (mirrors TypeScript)
# ---------------------------------------------------------------------------

MIN_SPEED: float = 0.25
MAX_SPEED: float = 4.0
MIN_TIMELINE_MS: float = 100  # anything shorter is treated as "whole video cut"

SIZES: List[str] = ["sm", "md", "lg", "xl"]

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class OpSuccess(TypedDict):
    ok: bool          # always True
    state: TimelineState
    notes: List[str]


class OpFailure(TypedDict):
    ok: bool          # always False
    error: EngineError


OpResult = Union[OpSuccess, OpFailure]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _err(code: str, message: str, details: Optional[Dict[str, object]] = None) -> EngineError:
    return EngineError(code=code, message=message, details=details or {})


def _validate_range(r: Interval, state: TimelineState) -> Optional[EngineError]:
    """Validate that *r* is a valid integer-ms interval within the source video."""
    if not isinstance(r.start, int) or not isinstance(r.end, int):
        return _err("INVALID_RANGE", "Range bounds must be whole milliseconds.", {"range": r})
    if r.start < 0 or r.end > state.sourceDurationMs:
        return _err(
            "RANGE_OUT_OF_BOUNDS",
            "Range lies outside the source video.",
            {"range": r, "sourceDurationMs": state.sourceDurationMs},
        )
    if r.end <= r.start:
        return _err("INVALID_RANGE", "Range end must be after its start.", {"range": r})
    return None


def _clone(state: TimelineState) -> TimelineState:
    """Return a shallow-but-independent copy of *state*.

    Dataclass fields that are frozen (Segment, Caption, AudioEffect) are safe
    to share; list containers are always copied so mutations to one state do
    not affect another.  This mirrors the TypeScript ``clone`` helper exactly.
    """
    return TimelineState(
        sourceDurationMs=state.sourceDurationMs,
        segments=list(state.segments),
        captions=[replace(c, wordIds=list(c.wordIds)) for c in state.captions],
        effects=list(state.effects),
        nextId=state.nextId,
    )


# ---------------------------------------------------------------------------
# Core dispatcher – mirrors ``applyOp`` in the TypeScript source
# ---------------------------------------------------------------------------

def apply_op(input_state: TimelineState, op: Op, words: Dict[str, Word]) -> OpResult:  # type: ignore[return]
    """Apply a single validated operation to *input_state*.

    Returns:
        ``{"ok": True,  "state": <new TimelineState>, "notes": [...]}`` on success.
        ``{"ok": False, "error": <EngineError>}`` on any validation failure.

    The *input_state* is **never** mutated.
    """
    state = _clone(input_state)
    notes: List[str] = []

    op_type: str = op["type"]  # type: ignore[literal-required]

    # ------------------------------------------------------------------
    # cut
    # ------------------------------------------------------------------
    if op_type == "cut":
        cut_op: CutOp = op  # type: ignore[assignment]
        if not cut_op["ranges"]:
            return {"ok": False, "error": _err("INVALID_OPERATION", "Cut has no ranges.")}
        for r in cut_op["ranges"]:
            err = _validate_range(r, state)
            if err:
                return {"ok": False, "error": err}
        removable = sum(present_ms(state.segments, r) for r in cut_op["ranges"])
        if removable < 1:
            return {
                "ok": False,
                "error": _err(
                    "NO_CHANGE",
                    "That content is already removed, so nothing was changed.",
                    {"ranges": cut_op["ranges"]},
                ),
            }
        segs = state.segments
        for r in cut_op["ranges"]:
            segs = subtract_range(segs, r)
        segs = normalize_segments(segs)
        if timeline_duration(segs) < MIN_TIMELINE_MS:
            return {
                "ok": False,
                "error": _err(
                    "WHOLE_VIDEO_CUT",
                    "This edit would remove the entire video, so it was not applied.",
                    {"ranges": cut_op["ranges"]},
                ),
            }
        state.segments = segs
        notes.append(f"Removed {removable / 1000:.3f}s of source content.")
        return {"ok": True, "state": state, "notes": notes}

    # ------------------------------------------------------------------
    # restore
    # ------------------------------------------------------------------
    if op_type == "restore":
        restore_op: RestoreOp = op  # type: ignore[assignment]
        for r in restore_op["ranges"]:
            err = _validate_range(r, state)
            if err:
                return {"ok": False, "error": err}

        # Collect which sub-intervals of the requested ranges are absent (cut).
        present = present_intervals(state.segments)
        missing: List[Interval] = []
        for r in restore_op["ranges"]:
            missing.extend(interval_minus(r, present))
        missing.sort(key=lambda i: i.start)

        if not missing:
            return {
                "ok": False,
                "error": _err(
                    "NO_CHANGE",
                    "Nothing in that range is cut, so nothing needs restoring.",
                    {"ranges": restore_op["ranges"]},
                ),
            }

        segs: List[Segment] = list(state.segments)
        for piece in missing:
            new_seg = Segment(srcStart=piece.start, srcEnd=piece.end, speed=1)
            # Prefer to insert next to the nearest source neighbour.
            idx = next((i for i, s in enumerate(segs) if s.srcEnd == piece.start), -1)
            if idx >= 0:
                segs.insert(idx + 1, new_seg)
                continue
            idx = next((i for i, s in enumerate(segs) if s.srcStart == piece.end), -1)
            if idx >= 0:
                segs.insert(idx, new_seg)
                continue
            # Fall back: insert after the last segment that ends before piece.start.
            pred = -1
            for i, s in enumerate(segs):
                if s.srcEnd <= piece.start and (pred < 0 or s.srcEnd > segs[pred].srcEnd):
                    pred = i
            if pred >= 0:
                segs.insert(pred + 1, new_seg)
            else:
                segs.insert(0, new_seg)

        state.segments = normalize_segments(segs)
        restored = sum(p.end - p.start for p in missing)
        notes.append(
            f"Restored {restored / 1000:.3f}s of source content at its original position (1x speed)."
        )
        return {"ok": True, "state": state, "notes": notes}

    # ------------------------------------------------------------------
    # speed
    # ------------------------------------------------------------------
    if op_type == "speed":
        speed_op: SpeedOp = op  # type: ignore[assignment]
        err = _validate_range(speed_op["range"], state)
        if err:
            return {"ok": False, "error": err}
        if not (MIN_SPEED <= speed_op["speed"] <= MAX_SPEED):
            return {
                "ok": False,
                "error": _err(
                    "INVALID_SPEED",
                    f"Speed must be between {MIN_SPEED}x and {MAX_SPEED}x.",
                    {"speed": speed_op["speed"]},
                ),
            }
        if present_ms(state.segments, speed_op["range"]) < 1:
            return {
                "ok": False,
                "error": _err(
                    "NO_VISIBLE_CONTENT",
                    "That range is entirely cut.",
                    {"range": speed_op["range"]},
                ),
            }
        r = speed_op["range"]
        segs = split_at(split_at(state.segments, r.start), r.end)
        changed = False
        new_segs: List[Segment] = []
        for s in segs:
            if s.srcStart >= r.start and s.srcEnd <= r.end and s.speed != speed_op["speed"]:
                changed = True
                new_segs.append(replace(s, speed=speed_op["speed"]))
            else:
                new_segs.append(s)
        if not changed:
            return {
                "ok": False,
                "error": _err(
                    "NO_CHANGE",
                    f"That range already plays at {speed_op['speed']}x.",
                    {"range": r},
                ),
            }
        state.segments = normalize_segments(new_segs)
        return {"ok": True, "state": state, "notes": notes}

    # ------------------------------------------------------------------
    # mute / bleep / clear_audio
    # ------------------------------------------------------------------
    if op_type in {"mute", "bleep", "clear_audio"}:
        audio_op: AudioOp = op  # type: ignore[assignment]
        word_ids: List[str] = audio_op["wordIds"]
        targets = [words.get(wid) for wid in word_ids]
        if not targets or any(w is None for w in targets):
            return {
                "ok": False,
                "error": _err(
                    "UNKNOWN_WORD",
                    "One or more word IDs do not exist.",
                    {"wordIds": word_ids},
                ),
            }

        if op_type == "clear_audio":
            before_count = len(state.effects)
            state.effects = [fx for fx in state.effects if fx.wordId not in word_ids]
            if len(state.effects) == before_count:
                return {
                    "ok": False,
                    "error": _err("NO_CHANGE", "Those words have no mute or bleep."),
                }
            return {"ok": True, "state": state, "notes": notes}

        # mute or bleep
        kind = op_type  # "mute" | "bleep"
        added = 0
        for w in targets:  # type: ignore[union-attr]
            if any(fx.wordId == w.id and fx.kind == kind for fx in state.effects):
                continue  # already has this effect
            state.effects.append(
                AudioEffect(
                    id=f"fx_{state.nextId}",
                    kind=kind,  # type: ignore[arg-type]
                    wordId=w.id,
                    srcStart=w.start,
                    srcEnd=w.end,
                )
            )
            state.nextId += 1
            added += 1
        if not added:
            verb = "muted" if kind == "mute" else "bleeped"
            return {"ok": False, "error": _err("NO_CHANGE", f"Those words are already {verb}.")}
        verb2 = "Muted" if kind == "mute" else "Bleeped"
        notes.append(f"{verb2} {added} word(s).")
        return {"ok": True, "state": state, "notes": notes}

    # ------------------------------------------------------------------
    # add_caption
    # ------------------------------------------------------------------
    if op_type == "add_caption":
        add_op: AddCaptionOp = op  # type: ignore[assignment]
        text = add_op["text"].strip()
        if not text or len(text) > 120:
            return {
                "ok": False,
                "error": _err("INVALID_CAPTION", "Caption text must be 1–120 characters."),
            }
        ws = [words.get(wid) for wid in add_op["wordIds"]]
        if not ws or any(w is None for w in ws):
            return {
                "ok": False,
                "error": _err("UNKNOWN_WORD", "Caption must anchor to existing transcript words."),
            }
        # Sort by transcript index for a deterministic word order.
        ws_sorted = sorted(ws, key=lambda w: w.index)  # type: ignore[union-attr]
        ids = [w.id for w in ws_sorted]  # type: ignore[union-attr]

        # Reject exact duplicate.
        if any(c.text == text and list(c.wordIds) == ids for c in state.captions):
            return {
                "ok": False,
                "error": _err("NO_CHANGE", "An identical caption already exists on those words."),
            }

        state.captions.append(
            Caption(
                id=f"cap_{state.nextId}",
                text=text,
                wordIds=ids,
                size="md",
                position="bottom",
            )
        )
        state.nextId += 1

        # Warn about anchored words that are currently cut.
        placed_segs = layout(state.segments)
        hidden = sum(
            1 for w in ws_sorted  # type: ignore[union-attr]
            if not place_word(placed_segs, w)["visible"]
        )
        if hidden:
            notes.append(
                f"{hidden} anchored word(s) are currently cut; the caption shows over the rest."
            )
        return {"ok": True, "state": state, "notes": notes}

    # ------------------------------------------------------------------
    # remove_caption
    # ------------------------------------------------------------------
    if op_type == "remove_caption":
        rem_op: RemoveCaptionOp = op  # type: ignore[assignment]
        before_count = len(state.captions)
        state.captions = [c for c in state.captions if c.id != rem_op["captionId"]]
        if len(state.captions) == before_count:
            return {
                "ok": False,
                "error": _err(
                    "UNKNOWN_CAPTION",
                    "That caption does not exist.",
                    {"captionId": rem_op["captionId"]},
                ),
            }
        return {"ok": True, "state": state, "notes": notes}

    # ------------------------------------------------------------------
    # style_caption
    # ------------------------------------------------------------------
    if op_type == "style_caption":
        style_op: StyleCaptionOp = op  # type: ignore[assignment]
        cap = next((c for c in state.captions if c.id == style_op["captionId"]), None)
        if cap is None:
            return {"ok": False, "error": _err("UNKNOWN_CAPTION", "That caption does not exist.")}

        changed = False
        new_size = cap.size
        new_position = cap.position

        size_step = style_op.get("sizeStep")
        if size_step:
            try:
                new_idx = SIZES.index(cap.size) + size_step
                if 0 <= new_idx < len(SIZES):
                    new_size = SIZES[new_idx]  # type: ignore[assignment]
                    changed = True
            except ValueError:
                pass

        new_pos_candidate = style_op.get("position")
        if new_pos_candidate and new_pos_candidate != cap.position:
            new_position = new_pos_candidate  # type: ignore[assignment]
            changed = True

        if not changed:
            return {"ok": False, "error": _err("NO_CHANGE", "The caption already has that style.")}

        # Replace the frozen Caption in the list with an updated copy.
        state.captions = [
            replace(c, size=new_size, position=new_position) if c.id == style_op["captionId"] else c
            for c in state.captions
        ]
        return {"ok": True, "state": state, "notes": notes}

    # ------------------------------------------------------------------
    # move (reorder)
    # ------------------------------------------------------------------
    if op_type == "move":
        move_op: MoveOp = op  # type: ignore[assignment]
        err = _validate_range(move_op["range"], state)
        if err:
            return {"ok": False, "error": err}

        r = move_op["range"]
        segs = split_at(split_at(state.segments, r.start), r.end)
        moving = [s for s in segs if s.srcStart >= r.start and s.srcEnd <= r.end]
        if not moving:
            return {
                "ok": False,
                "error": _err("NO_VISIBLE_CONTENT", "That range is entirely cut, nothing to move."),
            }
        rest = [s for s in segs if not (s.srcStart >= r.start and s.srcEnd <= r.end)]
        if not rest:
            return {"ok": False, "error": _err("NO_CHANGE", "Moving the whole video changes nothing.")}

        to = move_op["to"]
        if to == "start":
            insert_at = 0
        elif to == "end":
            insert_at = len(rest)
        else:
            # ``to`` is {"beforeSrc": <ms>}
            t: int = to["beforeSrc"]  # type: ignore[index]
            if r.start < t < r.end:
                return {
                    "ok": False,
                    "error": _err("INVALID_MOVE", "The destination lies inside the section being moved."),
                }
            rest = split_at(rest, t)
            insert_at = next((i for i, s in enumerate(rest) if s.srcStart == t), -1)
            if insert_at < 0:
                end_idx = next((i for i, s in enumerate(rest) if s.srcEnd == t), -1)
                if end_idx < 0:
                    return {
                        "ok": False,
                        "error": _err(
                            "DESTINATION_CUT",
                            "The destination time is not in the current timeline.",
                        ),
                    }
                insert_at = end_idx + 1

        new_segs = rest[:insert_at] + moving + rest[insert_at:]
        normalized = normalize_segments(new_segs)

        # Compare as plain dicts to match TS JSON.stringify comparison.
        def _seg_dict(s: Segment) -> dict:
            return {"srcStart": s.srcStart, "srcEnd": s.srcEnd, "speed": s.speed}

        if [_seg_dict(s) for s in normalized] == [_seg_dict(s) for s in state.segments]:
            return {"ok": False, "error": _err("NO_CHANGE", "The section is already at that position.")}

        state.segments = normalized
        return {"ok": True, "state": state, "notes": notes}

    # Unknown op type (should never happen with a typed Op union).
    return {  # type: ignore[return-value]
        "ok": False,
        "error": _err("UNKNOWN_OP", f"Operation type '{op_type}' is not supported."),
    }


# ---------------------------------------------------------------------------
# Multi-operation helper – mirrors ``applyOps`` in the TypeScript source
# ---------------------------------------------------------------------------

class ApplyOpsSuccess(TypedDict):
    ok: bool
    state: TimelineState
    notes: List[str]


class ApplyOpsFailure(TypedDict):
    ok: bool
    error: EngineError
    failedIndex: int


ApplyOpsResult = Union[ApplyOpsSuccess, ApplyOpsFailure]


def apply_ops(
    state: TimelineState,
    ops: List[Op],
    words: Dict[str, Word],
) -> ApplyOpsResult:
    """Apply a sequence of operations atomically (all-or-nothing).

    Returns ``{"ok": True, "state": ..., "notes": [...]}`` when every
    operation succeeds, or ``{"ok": False, "error": ..., "failedIndex": N}``
    when operation *N* fails (the original *state* is left completely
    untouched).
    """
    cur = state
    notes: List[str] = []
    for i, op in enumerate(ops):
        result = apply_op(cur, op, words)
        if not result["ok"]:
            return {
                "ok": False,
                "error": result["error"],  # type: ignore[typeddict-item]
                "failedIndex": i,
            }
        cur = result["state"]  # type: ignore[typeddict-item]
        notes.extend(result["notes"])  # type: ignore[typeddict-item]
    return {"ok": True, "state": cur, "notes": notes}
