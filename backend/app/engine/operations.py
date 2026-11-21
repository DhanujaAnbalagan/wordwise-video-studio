"""
Deterministic editing operations for VideoFlow AI.

This module mirrors the logic in `src/engine/operations.ts` while staying
pure‑functional: the input `TimelineState` is never mutated – a new state
object is returned on success. Errors are represented by the `EngineError`
dataclass defined in `types.py`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Tuple, Union

from .types import (
    AudioEffect,
    Caption,
    EngineError,
    Interval,
    Op,
    OpResult,
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
    CaptionSize,
    WORD_KEEP_FRACTION,
)
from .timeline import (
    layout,
    present_intervals,
    present_ms,
    split_at,
    subtract_range,
    normalize_segments,
    timeline_duration,
    interval_minus,
    place_word,
)

# ---------------------------------------------------------------------------
# Constants – copied from the TypeScript implementation
# ---------------------------------------------------------------------------
MIN_SPEED = 0.25
MAX_SPEED = 4.0
MIN_TIMELINE_MS = 100

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _err(code: str, message: str, details: Dict[str, object] | None = None) -> EngineError:
    return EngineError(code=code, message=message, details=details or {})


def _validate_range(r: Interval, state: TimelineState) -> EngineError | None:
    if not isinstance(r.start, int) or not isinstance(r.end, int):
        return _err(
            "INVALID_RANGE",
            "Range bounds must be whole milliseconds.",
            {"range": r},
        )
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
    """Create a shallow copy of the timeline state.

    The lists are copied and their items are shallow‑copied (dataclasses are
    immutable except for `TimelineState` itself). This mirrors the TS `clone`
    function which spreads the top‑level object and copies arrays.
    """
    return TimelineState(
        sourceDurationMs=state.sourceDurationMs,
        segments=[replace(s) for s in state.segments],
        captions=[replace(c) for c in state.captions],
        effects=[replace(e) for e in state.effects],
        nextId=state.nextId,
    )

# ---------------------------------------------------------------------------
# Core operation dispatcher – mirrors `applyOp` in the TS code.
# ---------------------------------------------------------------------------

def apply_op(input_state: TimelineState, op: Op, words: Dict[str, Word]) -> OpResult:
    """Apply a single validated operation to a timeline state.

    Returns a dict with either ``{"ok": True, "state": new_state, "notes": [...]}``
    or ``{"ok": False, "error": EngineError}``.
    The input state is never mutated.
    """
    state = _clone(input_state)
    notes: List[str] = []

    op_type = op["type"]

    # ---------- cut ----------
    if op_type == "cut":
        cut_op: CutOp = op  # type: ignore[assignment]
        if not cut_op["ranges"]:
            return {"ok": False, "error": _err("INVALID_OPERATION", "Cut has no ranges.")}
        for r in cut_op["ranges"]:
            e = _validate_range(r, state)
            if e:
                return {"ok": False, "error": e}
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
        notes.append(f"Removed {(removable / 1000):.3f}s of source content.")
        return {"ok": True, "state": state, "notes": notes}

    # ---------- restore ----------
    if op_type == "restore":
        restore_op: RestoreOp = op  # type: ignore[assignment]
        for r in restore_op["ranges"]:
            e = _validate_range(r, state)
            if e:
                return {"ok": False, "error": e}
        # Determine which pieces are missing (cut) within the requested ranges
        missing = (
            restore_op["ranges"]
            .flat_map(lambda r: interval_minus(r, present_intervals(state.segments)))  # type: ignore[arg-type]
        )
        # The above line uses a helper; in Python we implement with a loop
        # (the TypeScript version flattens intervalMinus over each range)
        missing_intervals: List[Interval] = []
        for r in restore_op["ranges"]:
            missing_intervals.extend(interval_minus(r, present_intervals(state.segments)))
        missing_intervals.sort(key=lambda i: i.start)
        if not missing_intervals:
            return {
                "ok": False,
                "error": _err(
                    "NO_CHANGE",
                    "Nothing in that range is cut, so nothing needs restoring.",
                    {"ranges": restore_op["ranges"]},
                ),
            }
        segs = list(state.segments)
        for piece in missing_intervals:
            seg = Interval(srcStart=piece.start, srcEnd=piece.end, speed=1)  # type: ignore[arg-type]
            # Insert next to its source neighbour so restored content returns to its original place.
            idx = next((i for i, s in enumerate(segs) if s.srcEnd == piece.start), -1)
            if idx >= 0:
                segs.insert(idx + 1, seg)  # type: ignore[arg-type]
                continue
            idx = next((i for i, s in enumerate(segs) if s.srcStart == piece.end), -1)
            if idx >= 0:
                segs.insert(idx, seg)  # type: ignore[arg-type]
                continue
            # Find the predecessor segment that ends before the piece starts
            pred = -1
            for i, s in enumerate(segs):
                if s.srcEnd <= piece.start and (pred < 0 or s.srcEnd > segs[pred].srcEnd):
                    pred = i
            if pred >= 0:
                segs.insert(pred + 1, seg)  # type: ignore[arg-type]
            else:
                segs.insert(0, seg)  # type: ignore[arg-type]
        segs = normalize_segments(segs)
        state.segments = segs
        restored = sum(p.end - p.start for p in missing_intervals)
        notes.append(f"Restored {(restored / 1000):.3f}s of source content at its original position (1x speed).")
        return {"ok": True, "state": state, "notes": notes}

    # ---------- speed ----------
    if op_type == "speed":
        speed_op: SpeedOp = op  # type: ignore[assignment]
        e = _validate_range(speed_op["range"], state)
        if e:
            return {"ok": False, "error": e}
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
        segs = split_at(split_at(state.segments, speed_op["range"].start), speed_op["range"].end)
        changed = False
        new_segs = []
        for s in segs:
            if (
                s.srcStart >= speed_op["range"].start
                and s.srcEnd <= speed_op["range"].end
                and s.speed != speed_op["speed"]
            ):
                changed = True
                new_segs.append(replace(s, speed=speed_op["speed"]))
            else:
                new_segs.append(s)
        if not changed:
            return {
                "ok": False,
                "error": _err(
                    "NO_CHANGE",
                    f"That range already plays at {speed_op["speed"]}x.",
                    {"range": speed_op["range"]},
                ),
            }
        state.segments = normalize_segments(new_segs)
        return {"ok": True, "state": state, "notes": notes}

    # ---------- audio effects (mute, bleep, clear_audio) ----------
    if op_type in {"mute", "bleep", "clear_audio"}:
        audio_op: AudioOp = op  # type: ignore[assignment]
        target_words = [words.get(wid) for wid in audio_op["wordIds"]]
        if not target_words or any(w is None for w in target_words):
            return {
                "ok": False,
                "error": _err(
                    "UNKNOWN_WORD",
                    "One or more word IDs do not exist.",
                    {"wordIds": audio_op["wordIds"]},
                ),
            }
        if op_type == "clear_audio":
            before = len(state.effects)
            state.effects = [fx for fx in state.effects if fx.wordId not in audio_op["wordIds"]]
            if len(state.effects) == before:
                return {"ok": False, "error": _err("NO_CHANGE", "Those words have no mute or bleep.")}
            return {"ok": True, "state": state, "notes": notes}
        added = 0
        for w in target_words:  # type: ignore[arg-type]
            if any(fx.wordId == w.id and fx.kind == op_type for fx in state.effects):
                continue
            state.effects.append(
                AudioEffect(
                    id=f"fx_{state.nextId}",
                    kind=op_type,  # type: ignore[arg-type]
                    wordId=w.id,
                    srcStart=w.start,
                    srcEnd=w.end,
                )
            )
            state.nextId += 1
            added += 1
        if added == 0:
            return {
                "ok": False,
                "error": _err(
                    "NO_CHANGE",
                    f"Those words are already {'muted' if op_type == 'mute' else 'bleeped'}.",
                ),
            }
        notes.append(f"{'Muted' if op_type == 'mute' else 'Bleeped'} {added} word(s).")
        return {"ok": True, "state": state, "notes": notes}

    # ---------- add_caption ----------
    if op_type == "add_caption":
        add_op: AddCaptionOp = op  # type: ignore[assignment]
        text = add_op["text"].strip()
        if not text or len(text) > 120:
            return {"ok": False, "error": _err("INVALID_CAPTION", "Caption text must be 1–120 characters.")}
        ws = [words.get(wid) for wid in add_op["wordIds"]]
        if not ws or any(w is None for w in ws):
            return {"ok": False, "error": _err("UNKNOWN_WORD", "Caption must anchor to existing transcript words.")}
        # Sort by transcript index to keep deterministic ordering
        ws_sorted = sorted(ws, key=lambda w: w.index)  # type: ignore[arg-type]
        ids = [w.id for w in ws_sorted]
        # Deduplicate identical caption
        if any(c.text == text and c.wordIds == ids for c in state.captions):
            return {"ok": False, "error": _err("NO_CHANGE", "An identical caption already exists on those words.")}
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
        placed_segments = layout(state.segments)
        hidden = sum(1 for w in ws_sorted if not place_word(placed_segments, w)["visible"])
        if hidden:
            notes.append(f"{hidden} anchored word(s) are currently cut; the caption shows over the rest.")
        return {"ok": True, "state": state, "notes": notes}

    # ---------- remove_caption ----------
    if op_type == "remove_caption":
        rem_op: RemoveCaptionOp = op  # type: ignore[assignment]
        before = len(state.captions)
        state.captions = [c for c in state.captions if c.id != rem_op["captionId"]]
        if len(state.captions) == before:
            return {
                "ok": False,
                "error": _err(
                    "UNKNOWN_CAPTION",
                    "That caption does not exist.",
                    {"captionId": rem_op["captionId"]},
                ),
            }
        return {"ok": True, "state": state, "notes": notes}

    # ---------- style_caption ----------
    if op_type == "style_caption":
        style_op: StyleCaptionOp = op  # type: ignore[assignment]
        cap = next((c for c in state.captions if c.id == style_op["captionId"]), None)
        if cap is None:
            return {"ok": False, "error": _err("UNKNOWN_CAPTION", "That caption does not exist.")}
        changed = False
        # Size stepping
        if style_op.get("sizeStep"):
            sizes: List[CaptionSize] = ["sm", "md", "lg", "xl"]
            try:
                idx = sizes.index(cap.size) + style_op["sizeStep"]  # type: ignore[arg-type]
                if 0 <= idx < len(sizes):
                    cap.size = sizes[idx]
                    changed = True
            except ValueError:
                pass
        # Position change
        if style_op.get("position") and style_op["position"] != cap.position:
            cap.position = style_op["position"]  # type: ignore[arg-type]
            changed = True
        if not changed:
            return {"ok": False, "error": _err("NO_CHANGE", "The caption already has that style.")}
        return {"ok": True, "state": state, "notes": notes}

    # ---------- move (reorder) ----------
    if op_type == "move":
        move_op: MoveOp = op  # type: ignore[assignment]
        e = _validate_range(move_op["range"], state)
        if e:
            return {"ok": False, "error": e}
        # Split the timeline so the moved region becomes isolated
        segs = split_at(split_at(state.segments, move_op["range"].start), move_op["range"].end)
        moving = [s for s in segs if s.srcStart >= move_op["range"].start and s.srcEnd <= move_op["range"].end]
        if not moving:
            return {"ok": False, "error": _err("NO_VISIBLE_CONTENT", "That range is entirely cut, nothing to move.")}
        rest = [s for s in segs if not (s.srcStart >= move_op["range"].start and s.srcEnd <= move_op["range"].end)]
        if not rest:
            return {"ok": False, "error": _err("NO_CHANGE", "Moving the whole video changes nothing.")}
        # Determine insertion point
        if move_op["to"] == "start":
            insert_at = 0
        elif move_op["to"] == "end":
            insert_at = len(rest)
        else:
            # Destination is a specific source timestamp
            t = move_op["to"]["beforeSrc"]  # type: ignore[index]
            if move_op["range"].start < t < move_op["range"].end:
                return {"ok": False, "error": _err("INVALID_MOVE", "The destination lies inside the section being moved.")}
            # Split the rest at the destination time to create a clean insertion point
            rest = split_at(rest, t)
            insert_at = next((i for i, s in enumerate(rest) if s.srcStart == t), -1)
            if insert_at < 0:
                # Destination may be exactly at a segment end
                end_idx = next((i for i, s in enumerate(rest) if s.srcEnd == t), -1)
                if end_idx < 0:
                    return {"ok": False, "error": _err("DESTINATION_CUT", "The destination time is not in the current timeline.")}
                insert_at = end_idx + 1
        new_order = rest[:insert_at] + moving + rest[insert_at:]
        normalized = normalize_segments(new_order)
        if normalized == state.segments:
            return {"ok": False, "error": _err("NO_CHANGE", "The section is already at that position.")}
        state.segments = normalized
        return {"ok": True, "state": state, "notes": notes}

    # If we reach here, the operation type is unknown (should never happen)
    return {"ok": False, "error": _err("UNKNOWN_OP", f"Operation type {op_type} is not supported.")}

# ---------------------------------------------------------------------------
# Multi‑operation helper – mirrors `applyOps`
# ---------------------------------------------------------------------------

def apply_ops(state: TimelineState, ops: List[Op], words: Dict[str, Word]) -> Union[Tuple[bool, TimelineState, List[str]], Tuple[bool, EngineError, int]]:
    """Apply a sequence of operations atomically.

    Returns ``(True, new_state, notes)`` on total success, or ``(False, error,
    failed_index)`` if any operation fails. The original ``state`` is left
    untouched.
    """
    cur = state
    notes: List[str] = []
    for i, op in enumerate(ops):
        result = apply_op(cur, op, words)
        if not result["ok"]:
            return (False, result["error"], i)  # type: ignore[return-value]
        cur = result["state"]
        notes.extend(result["notes"])
    return (True, cur, notes)
"""
