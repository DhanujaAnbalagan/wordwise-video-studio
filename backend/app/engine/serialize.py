"""JSON (de)serialisation and canonical hashing for engine objects.

The wire format mirrors the TypeScript engine (``src/engine/types.ts``) so a
timeline produced by the browser and by the Python backend serialise to the
same canonical JSON. Integral floats (``1.0``) are emitted as integers (``1``)
because JavaScript's ``JSON.stringify`` does the same; this keeps hashes
identical across both engines and makes ``speed=1`` and ``speed=1.0`` the same
timeline.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Mapping

from .types import (
    AudioEffect,
    Caption,
    Interval,
    Segment,
    TextOverlay,
    TimelineState,
    Transcript,
    Word,
)

# ---------------------------------------------------------------------------
# Canonical JSON + hashing
# ---------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    """Convert dataclasses/tuples to plain JSON values; integral floats -> int."""
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("Non-finite numbers cannot be serialised.")
        return int(value) if value.is_integer() else value
    return value


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, ``None`` fields dropped."""
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Any) -> str:
    """SHA-256 (first 32 hex chars) of the canonical JSON of ``value``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


def word_from_dict(d: Mapping[str, Any]) -> Word:
    return Word(
        id=str(d["id"]),
        index=int(d["index"]),
        text=str(d["text"]),
        start=int(d["start"]),
        end=int(d["end"]),
        speaker=d.get("speaker"),
        confidence=float(d["confidence"]) if d.get("confidence") is not None else None,
    )


def transcript_from_dict(d: Mapping[str, Any]) -> Transcript:
    return Transcript(
        provider=str(d.get("provider", "unknown")),
        model=d.get("model"),
        note=d.get("note"),
        durationMs=int(d["durationMs"]),
        speakers={str(k): {"label": str(v.get("label", k))} for k, v in (d.get("speakers") or {}).items()},
        words=[word_from_dict(w) for w in d.get("words", [])],
    )


def transcript_to_dict(t: Transcript) -> Dict[str, Any]:
    return _plain(t)


# ---------------------------------------------------------------------------
# Timeline state
# ---------------------------------------------------------------------------


def state_to_dict(state: TimelineState) -> Dict[str, Any]:
    """Plain-dict snapshot. Always a fresh object tree (no shared nested data)."""
    return _plain(state)


def state_from_dict(d: Mapping[str, Any]) -> TimelineState:
    """Build a TimelineState from its JSON form. Nested data is deep-copied."""
    d = copy.deepcopy(dict(d))
    return TimelineState(
        sourceDurationMs=int(d["sourceDurationMs"]),
        segments=[Segment(srcStart=int(s["srcStart"]), srcEnd=int(s["srcEnd"]), speed=float(s["speed"])) for s in d.get("segments", [])],
        captions=[
            Caption(id=c["id"], text=c["text"], wordIds=list(c["wordIds"]), size=c.get("size", "md"), position=c.get("position", "bottom"))
            for c in d.get("captions", [])
        ],
        effects=[
            AudioEffect(id=e["id"], kind=e["kind"], wordId=e["wordId"], srcStart=int(e["srcStart"]), srcEnd=int(e["srcEnd"]))
            for e in d.get("effects", [])
        ],
        overlays=[
            TextOverlay(
                id=o["id"],
                text=o["text"],
                anchorType=o["anchorType"],
                startMs=int(o["startMs"]),
                endMs=int(o["endMs"]),
                position=o.get("position", "bottom"),
                style=dict(o["style"]) if o.get("style") else None,
            )
            for o in d.get("overlays", [])
        ],
        nextId=int(d.get("nextId", 1)),
    )


def clone_state(state: TimelineState) -> TimelineState:
    """Fully independent deep copy (round-trips through the JSON form)."""
    return state_from_dict(state_to_dict(state))


def initial_state(source_duration_ms: int) -> TimelineState:
    return TimelineState(
        sourceDurationMs=int(source_duration_ms),
        segments=[Segment(srcStart=0, srcEnd=int(source_duration_ms), speed=1.0)],
    )


def timeline_hash(state: TimelineState) -> str:
    """Identity of the edit, excluding counters (``nextId``).

    Effects are sorted so the hash does not depend on insertion order.
    """
    d = state_to_dict(state)
    d.pop("nextId", None)
    d["effects"] = sorted(d.get("effects", []), key=lambda e: (e["srcStart"], e["wordId"], e["kind"]))
    return stable_hash(d)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def _interval(v: Any) -> Interval:
    if isinstance(v, Interval):
        return v
    return Interval(start=int(v["start"]), end=int(v["end"]))


def op_from_dict(d: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert a JSON operation into the engine's Op form (Interval objects)."""
    op: Dict[str, Any] = copy.deepcopy(dict(d))
    if "ranges" in op:
        op["ranges"] = [_interval(r) for r in op["ranges"]]
    if "range" in op:
        op["range"] = _interval(op["range"])
    if op.get("type") in ("cut", "keep", "restore"):
        op.setdefault("reason", None)
    if op.get("type") == "speed":
        op["speed"] = float(op["speed"])
    if op.get("type") == "style_caption":
        op.setdefault("sizeStep", None)
        op.setdefault("position", None)
    if op.get("type") == "add_text_overlay":
        op.setdefault("position", None)
        op.setdefault("style", None)
    return op


def op_to_dict(op: Mapping[str, Any]) -> Dict[str, Any]:
    return _plain(dict(op))


def ops_to_list(ops: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [op_to_dict(o) for o in ops]
