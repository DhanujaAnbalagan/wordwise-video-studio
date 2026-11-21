"""Validation utilities for the VideoFlow AI editing engine.

The functions mirror the validation logic from the TypeScript
implementation (src/engine/operations.ts). They are pure – they never
mutate the supplied objects – and they return an ``EngineError`` when a
validation rule fails, otherwise ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, List

from .types import (
    Interval,
    Segment,
    TimelineState,
    EngineError,
    Caption,
    AudioEffect,
    Word,
)

# Constants matching the TypeScript engine
MIN_SPEED = 0.25
MAX_SPEED = 4.0
MIN_CAPTION_LENGTH = 1
MAX_CAPTION_LENGTH = 120


def _make_error(code: str, message: str, details: Optional[Dict] = None) -> EngineError:
    """Create an ``EngineError`` consistent with the TS ``err`` helper.

    Args:
        code: Short error identifier.
        message: Human‑readable description.
        details: Optional mapping with extra diagnostic data.
    """
    return EngineError(code=code, message=message, details=details or {})


# ---------------------------------------------------------------------------
# Range validation (mirrors ``validateRange`` in src/engine/operations.ts)
# ---------------------------------------------------------------------------
def validate_range(r: Interval, state: TimelineState) -> Optional[EngineError]:
    """Validate a source‑time interval.

    The rules are:
    * ``start`` and ``end`` must be integers (whole milliseconds).
    * ``start`` must be >= 0 and ``end`` must be <= ``state.sourceDurationMs``.
    * ``end`` must be greater than ``start``.

    Returns an ``EngineError`` if any rule fails, otherwise ``None``.
    """
    # 1. integer bounds
    if not isinstance(r.start, int) or not isinstance(r.end, int):
        return _make_error(
            "INVALID_RANGE",
            "Range bounds must be whole milliseconds.",
            {"range": r},
        )
    # 2. bounds within source duration
    if r.start < 0 or r.end > state.sourceDurationMs:
        return _make_error(
            "RANGE_OUT_OF_BOUNDS",
            "Range lies outside the source video.",
            {"range": r, "sourceDurationMs": state.sourceDurationMs},
        )
    # 3. start before end
    if r.end <= r.start:
        return _make_error(
            "INVALID_RANGE",
            "Range end must be after its start.",
            {"range": r},
        )
    return None


# ---------------------------------------------------------------------------
# Segment validation
# ---------------------------------------------------------------------------
def validate_segment(seg: Segment) -> Optional[EngineError]:
    """Validate a single ``Segment``.

    * ``srcStart`` must be < ``srcEnd``.
    * ``speed`` must be a finite positive number.
    """
    if seg.srcEnd <= seg.srcStart:
        return _make_error(
            "INVALID_SEGMENT",
            "Segment end must be after start.",
            {"segment": seg},
        )
    if not isinstance(seg.speed, (int, float)):
        return _make_error(
            "INVALID_SPEED",
            "Speed must be a number.",
            {"segment": seg},
        )
    if seg.speed <= 0 or seg.speed != seg.speed or seg.speed in (float("inf"), float("-inf")):
        return _make_error(
            "INVALID_SPEED",
            "Speed must be a finite, positive number.",
            {"segment": seg},
        )
    return None


# ---------------------------------------------------------------------------
# Speed validation (used for ``speed`` operations)
# ---------------------------------------------------------------------------
def validate_speed(speed: float) -> Optional[EngineError]:
    """Validate a playback speed against engine limits.

    The TypeScript constants are ``MIN_SPEED = 0.25`` and ``MAX_SPEED = 4``.
    """
    if not isinstance(speed, (int, float)):
        return _make_error(
            "INVALID_SPEED",
            "Speed must be a number.",
            {"speed": speed},
        )
    if speed != speed or speed in (float("inf"), float("-inf")):
        return _make_error(
            "INVALID_SPEED",
            "Speed must be a finite number.",
            {"speed": speed},
        )
    if not (MIN_SPEED <= speed <= MAX_SPEED):
        return _make_error(
            "INVALID_SPEED",
            f"Speed must be between {MIN_SPEED}x and {MAX_SPEED}x.",
            {"speed": speed},
        )
    return None


# ---------------------------------------------------------------------------
# Caption validation
# ---------------------------------------------------------------------------
def validate_caption(caption: Caption, words_by_id: Dict[str, Word]) -> Optional[EngineError]:
    """Validate a caption definition.

    * Text length must be between ``MIN_CAPTION_LENGTH`` and
      ``MAX_CAPTION_LENGTH``.
    * Every ``wordId`` must exist in ``words_by_id``.
    """
    txt = caption.text.strip()
    if not (MIN_CAPTION_LENGTH <= len(txt) <= MAX_CAPTION_LENGTH):
        return _make_error(
            "INVALID_CAPTION",
            "Caption text must be 1–120 characters.",
            {"caption": caption},
        )
    if not caption.wordIds:
        return _make_error(
            "INVALID_CAPTION",
            "Caption must anchor to at least one word.",
            {"caption": caption},
        )
    missing = [wid for wid in caption.wordIds if wid not in words_by_id]
    if missing:
        return _make_error(
            "UNKNOWN_WORD",
            "Caption references word IDs that do not exist.",
            {"missingWordIds": missing, "caption": caption},
        )
    return None


# ---------------------------------------------------------------------------
# Audio effect validation
# ---------------------------------------------------------------------------
VALID_EFFECT_KINDS = {"mute", "bleep"}


def validate_audio_effect(effect: AudioEffect, words_by_id: Dict[str, Word]) -> Optional[EngineError]:
    """Validate an ``AudioEffect`` entry.

    * ``kind`` must be one of the allowed effect kinds.
    * ``wordId`` must exist in ``words_by_id``.
    """
    if effect.kind not in VALID_EFFECT_KINDS:
        return _make_error(
            "INVALID_EFFECT_KIND",
            f"Effect kind must be one of {sorted(VALID_EFFECT_KINDS)}.",
            {"effect": effect},
        )
    if effect.wordId not in words_by_id:
        return _make_error(
            "UNKNOWN_WORD",
            "Audio effect references a word ID that does not exist.",
            {"effect": effect},
        )
    return None


# ---------------------------------------------------------------------------
# Timeline‑state consistency (lightweight sanity check)
# ---------------------------------------------------------------------------
def validate_timeline_state(state: TimelineState) -> Optional[EngineError]:
    """Perform a thin sanity‑check on the overall ``TimelineState``.

    Currently we ensure:
    * ``sourceDurationMs`` is non‑negative.
    * All segments are internally valid (using ``validate_segment``).
    * No overlapping segments (the engine permits gaps but not overlap).
    """
    if state.sourceDurationMs < 0:
        return _make_error(
            "INVALID_STATE",
            "sourceDurationMs must be non‑negative.",
            {"sourceDurationMs": state.sourceDurationMs},
        )
    # Validate each segment individually
    for seg in state.segments:
        err = validate_segment(seg)
        if err:
            return err
    # Detect overlapping source intervals
    sorted_segs = sorted(state.segments, key=lambda s: s.srcStart)
    for i in range(1, len(sorted_segs)):
        prev = sorted_segs[i - 1]
        cur = sorted_segs[i]
        if cur.srcStart < prev.srcEnd:
            return _make_error(
                "OVERLAPPING_SEGMENTS",
                "Segments must not overlap in source time.",
                {"prev": prev, "cur": cur},
            )
    return None
