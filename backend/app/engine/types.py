"""Python equivalents of the TypeScript engine types.
The design mirrors `src/engine/types.ts` but uses Python dataclasses and
typing. All time values are integer milliseconds of *source* video time unless
explicitly marked as a timeline time (not stored as identity).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Literal, Optional, TypedDict, Union

# Basic time unit – integer milliseconds of source video.
Ms = int

# Simple interval on source time.
@dataclass(frozen=True)
class Interval:
    start: Ms
    end: Ms

# Transcript word – stable identifier.
@dataclass(frozen=True)
class Word:
    id: str
    index: int
    text: str
    start: Ms
    end: Ms
    speaker: Optional[str] = None
    confidence: Optional[float] = None

# Transcript container.
@dataclass(frozen=True)
class Transcript:
    provider: str
    model: Optional[str]
    note: Optional[str]
    durationMs: Ms
    speakers: Dict[str, Dict[str, str]]  # {id: {label: str}}
    words: List[Word]

# A piece of the original source video.
@dataclass(frozen=True)
class Segment:
    srcStart: Ms
    srcEnd: Ms
    speed: float

CaptionSize = Literal["sm", "md", "lg", "xl"]

# Caption anchored to word IDs.
@dataclass(frozen=True)
class Caption:
    id: str
    text: str
    wordIds: List[str]
    size: CaptionSize
    position: Literal["bottom", "top"]

# Audio effect anchored to a word.
@dataclass(frozen=True)
class AudioEffect:
    id: str
    kind: Literal["mute", "bleep"]
    wordId: str
    srcStart: Ms
    srcEnd: Ms

OverlayAnchorType = Literal["source", "timeline"]

# Text overlay with explicit source-time or timeline-time anchoring.
@dataclass(frozen=True)
class TextOverlay:
    id: str
    text: str
    anchorType: OverlayAnchorType
    startMs: Ms
    endMs: Ms
    position: Literal["bottom", "top", "center"] = "bottom"
    style: Optional[Dict[str, str]] = None

# Full mutable timeline state – identical to TS `TimelineState` plus overlays.
@dataclass
class TimelineState:
    sourceDurationMs: Ms
    segments: List[Segment] = field(default_factory=list)
    captions: List[Caption] = field(default_factory=list)
    effects: List[AudioEffect] = field(default_factory=list)
    overlays: List[TextOverlay] = field(default_factory=list)
    nextId: int = 1

# Operations – using TypedDict for flexibility.
class CutOp(TypedDict):
    type: Literal["cut"]
    ranges: List[Interval]
    reason: Optional[str]

class KeepOp(TypedDict):
    type: Literal["keep"]
    ranges: List[Interval]
    reason: Optional[str]

class RestoreOp(TypedDict):
    type: Literal["restore"]
    ranges: List[Interval]
    reason: Optional[str]

class SpeedOp(TypedDict):
    type: Literal["speed"]
    range: Interval
    speed: float

class AudioOp(TypedDict):
    type: Literal["mute", "bleep", "clear_audio"]
    wordIds: List[str]

class AddCaptionOp(TypedDict):
    type: Literal["add_caption"]
    text: str
    wordIds: List[str]

class RemoveCaptionOp(TypedDict):
    type: Literal["remove_caption"]
    captionId: str

class StyleCaptionOp(TypedDict):
    type: Literal["style_caption"]
    captionId: str
    sizeStep: Optional[int]
    position: Optional[Literal["top", "bottom"]]

class MoveOp(TypedDict):
    type: Literal["move"]
    range: Interval
    to: Union[Literal["start", "end"], Dict[str, Ms]]  # {beforeSrc: Ms}

class AddTextOverlayOp(TypedDict):
    type: Literal["add_text_overlay"]
    text: str
    anchorType: OverlayAnchorType
    startMs: Ms
    endMs: Ms
    position: Optional[Literal["bottom", "top", "center"]]
    style: Optional[Dict[str, str]]

class RemoveTextOverlayOp(TypedDict):
    type: Literal["remove_text_overlay"]
    overlayId: str

Op = Union[CutOp, KeepOp, RestoreOp, SpeedOp, AudioOp, AddCaptionOp,
           RemoveCaptionOp, StyleCaptionOp, MoveOp, AddTextOverlayOp, RemoveTextOverlayOp]

# Instruction outcome.
InstructionStatus = Literal["applied", "needs_clarification", "rejected"]

@dataclass(frozen=True)
class EngineError:
    code: str
    message: str
    retryable: bool = False
    details: Dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class ClarificationOption:
    label: str
    detail: Optional[str]
    ops: List[Op]

@dataclass(frozen=True)
class StateSummary:
    durationTl: int
    segments: int
    visibleWords: int
    captionsVisible: int
    captionsHidden: int
    effects: int
    hash: str

@dataclass(frozen=True)
class InstructionResult:
    id: str
    input: str
    status: InstructionStatus
    createdAt: int
    latencyMs: int
    message: str
    notes: List[str]
    ops: Optional[List[Op]] = None
    revisionId: Optional[str] = None
    before: Optional[StateSummary] = None
    after: Optional[StateSummary] = None
    question: Optional[str] = None
    options: Optional[List[ClarificationOption]] = None
    basedOnHash: Optional[str] = None
    resolvedWith: Optional[int] = None
    error: Optional[EngineError] = None

# Revision and History structures.
@dataclass(frozen=True)
class Revision:
    id: str
    index: int
    parentId: Optional[str]
    kind: Literal["initial", "edit", "restore"]
    label: str
    ops: List[Op]
    state: TimelineState
    hash: str
    createdAt: int

@dataclass
class History:
    revisions: List[Revision] = field(default_factory=list)
    undo: List[str] = field(default_factory=list)  # revision IDs, head last
    redo: List[str] = field(default_factory=list)
    sourceVideoPath: Optional[str] = None
    sourceHash: Optional[str] = None
