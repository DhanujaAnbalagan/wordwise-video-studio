"""RenderPlan data model and construction for VideoFlow AI.

Converts timeline edits, cuts, speed changes, reordering, captions and overlays
into a deterministic, executable FFmpeg render plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from ..engine.types import Caption, Ms, Segment, TextOverlay, TimelineState, Transcript, Word
from ..engine.timeline import layout, place_text_overlay, timeline_duration, timeline_hash, PlacedSegment
from .caption_mapper import resolve_captions, ResolvedCaption, MIN_CAPTION_MS


@dataclass
class FilterStep:
    """A single segment filter step for FFmpeg.

    Attributes
    ----------
    video_filter : Optional[str]
        Filter expression for the video stream (e.g. trim+setpts).
    audio_filter : Optional[str]
        Filter expression for the audio stream (e.g. atrim+asetpts+atempo).
    start_ms : int
        Segment start in source time (ms).
    end_ms : int
        Segment end in source time (ms).
    speed : float
        Playback speed multiplier.
    tl_start_ms : int
        Segment start offset in timeline time (ms).
    tl_end_ms : int
        Segment end offset in timeline time (ms).
    source_id : Optional[str]
        Identifier of the input source media.
    """
    video_filter: Optional[str] = None
    audio_filter: Optional[str] = None
    start_ms: int = 0
    end_ms: int = 0
    speed: float = 1.0
    tl_start_ms: int = 0
    tl_end_ms: int = 0
    source_id: Optional[str] = "src0"


@dataclass
class CaptionOverlay:
    """A caption or text overlay descriptor to be drawn via drawtext.

    Attributes
    ----------
    text : str
        Text string to render.
    start_ms : int
        Timeline start time in milliseconds.
    end_ms : int
        Timeline end time in milliseconds.
    x : Union[int, str]
        Horizontal expression or coordinate for FFmpeg drawtext.
    y : Union[int, str]
        Vertical expression or coordinate for FFmpeg drawtext.
    font_size : int
        Font size in pixels.
    font_color : str
        Font color name or hex.
    box : bool
        Whether to draw a background bounding box.
    box_color : str
        Color of the background bounding box.
    box_border_w : int
        Border padding width around text.
    id : Optional[str]
        Unique identifier of the caption/overlay.
    size : str
        Size label ("sm", "md", "lg", "xl").
    position : str
        Vertical position category ("top", "center", "bottom").
    source_id : Optional[str]
        Source identifier.
    """
    text: str
    start_ms: int
    end_ms: int
    x: Union[int, str] = "(w-text_w)/2"
    y: Union[int, str] = "h-text_h-40"
    font_size: int = 30
    font_color: str = "white"
    box: bool = True
    box_color: str = "black@0.6"
    box_border_w: int = 12
    id: Optional[str] = None
    size: str = "md"
    position: str = "bottom"
    source_id: Optional[str] = None


@dataclass
class RenderPlan:
    """Complete, deterministic FFmpeg rendering specification."""
    filter_steps: List[FilterStep] = field(default_factory=list)
    captions: List[CaptionOverlay] = field(default_factory=list)
    overlays: List[CaptionOverlay] = field(default_factory=list)
    expected_duration_ms: int = 0
    timeline_hash: Optional[str] = None
    has_video: bool = True
    has_audio: bool = True
    warnings: List[str] = field(default_factory=list)


def atempo_chain(speed: float) -> str:
    """Build an FFmpeg atempo filter chain for audio speed adjustment.

    FFmpeg atempo filter supports values between 0.5 and 2.0. Values outside
    this range must be chained.
    """
    s = float(speed)
    if abs(s - 1.0) < 1e-6:
        return ""
    parts: List[str] = []
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def build_render_plan(
    state: TimelineState,
    transcript: Optional[Transcript] = None,
    has_video: bool = True,
    has_audio: bool = True,
) -> RenderPlan:
    """Convert a TimelineState into a deterministic RenderPlan.

    Preserves source timestamps and anchors, correctly translating them to
    timeline coordinates. Fully removed captions are omitted, partially retained
    captions are clipped according to surviving words, and minimum 700 ms duration
    is enforced.
    """
    placed: List[PlacedSegment] = layout(state.segments)
    warnings: List[str] = []
    filter_steps: List[FilterStep] = []

    for p in placed:
        src_start_s = f"{p.srcStart / 1000.0:.3f}"
        src_end_s = f"{p.srcEnd / 1000.0:.3f}"

        # Video filter
        v_filter: Optional[str] = None
        if has_video:
            v_filter = f"trim=start={src_start_s}:end={src_end_s},setpts=(PTS-STARTPTS)/{p.speed}"

        # Audio filter
        a_filter: Optional[str] = None
        if has_audio:
            tempo = atempo_chain(p.speed)
            if tempo:
                a_filter = f"atrim=start={src_start_s}:end={src_end_s},asetpts=PTS-STARTPTS,{tempo}"
            else:
                a_filter = f"atrim=start={src_start_s}:end={src_end_s},asetpts=PTS-STARTPTS"

        filter_steps.append(
            FilterStep(
                video_filter=v_filter,
                audio_filter=a_filter,
                start_ms=p.srcStart,
                end_ms=p.srcEnd,
                speed=p.speed,
                tl_start_ms=int(round(p.tlStart)),
                tl_end_ms=int(round(p.tlEnd)),
                source_id="src0",
            )
        )

    # Caption mapping
    caption_overlays: List[CaptionOverlay] = []
    if transcript and transcript.words and state.captions:
        words_by_id = {w.id: w for w in transcript.words}
        resolved_caps = resolve_captions(state, words_by_id, placed)
        font_size_map = {"sm": 22, "md": 30, "lg": 40, "xl": 52}

        for rc in resolved_caps:
            if rc.status == "removed":
                warnings.append(f"Caption '{rc.caption.text}' omitted: all anchored words cut.")
                continue
            if rc.status == "partial":
                warnings.append(f"Caption '{rc.caption.text}' partially retained: {rc.kept_words}/{rc.total_words} words visible.")

            y_pos = "40" if rc.caption.position == "top" else "h-text_h-40"
            f_size = font_size_map.get(rc.caption.size, 30)

            for span in rc.spans:
                caption_overlays.append(
                    CaptionOverlay(
                        text=rc.caption.text,
                        start_ms=int(round(span.tlStart)),
                        end_ms=int(round(span.tlEnd)),
                        x="(w-text_w)/2",
                        y=y_pos,
                        font_size=f_size,
                        font_color="white",
                        box=True,
                        box_color="black@0.6",
                        box_border_w=12,
                        id=rc.caption.id,
                        size=rc.caption.size,
                        position=rc.caption.position,
                    )
                )

    # Overlay mapping (TextOverlay)
    text_overlays: List[CaptionOverlay] = []
    if getattr(state, "overlays", None):
        for ov in state.overlays:
            placed_ov = place_text_overlay(placed, ov)
            if not placed_ov.visible:
                continue

            if ov.position == "top":
                y_pos = "40"
            elif ov.position == "center":
                y_pos = "(h-text_h)/2"
            else:
                y_pos = "h-text_h-40"

            for span in placed_ov.spans:
                text_overlays.append(
                    CaptionOverlay(
                        text=ov.text,
                        start_ms=int(round(span.tlStart)),
                        end_ms=int(round(span.tlEnd)),
                        x="(w-text_w)/2",
                        y=y_pos,
                        font_size=30,
                        font_color="white",
                        box=True,
                        box_color="black@0.6",
                        box_border_w=12,
                        id=ov.id,
                        position=ov.position,
                    )
                )

    expected_dur = int(round(timeline_duration(state.segments)))
    t_hash = timeline_hash(state)

    return RenderPlan(
        filter_steps=filter_steps,
        captions=caption_overlays,
        overlays=text_overlays,
        expected_duration_ms=expected_dur,
        timeline_hash=t_hash,
        has_video=has_video,
        has_audio=has_audio,
        warnings=warnings,
    )
