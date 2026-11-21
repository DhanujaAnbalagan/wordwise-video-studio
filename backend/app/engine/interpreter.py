"""Natural language instruction interpreter for VideoFlow AI.
Translates user editing instructions into structured, validated operation plans.
Mirrors `src/engine/interpreter.ts` and extends it with structured Pydantic operation plans.
The interpreter never mutates state; all modifications are executed by the deterministic engine.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field

from .operations import MAX_SPEED, MIN_SPEED
from .time_utils import RANGE_RE, TIME_PATTERN, format_tc, parse_time
from .timeline import (
    Interval,
    PlacedSegment,
    Segment,
    TimelineState,
    layout,
    place_word,
    present_intervals,
    timeline_duration,
    tl_range_to_src,
)
from .transcript import (
    context_of,
    is_filler,
    lead_ins,
    norm_token,
    related_terms,
    search_phrase,
    sentence_of,
    sentences,
    snap_to_words,
    words_in_range,
)
from .types import ClarificationOption, Op, Transcript, Word


# ---------------------------------------------------------------------------
# Structured Operation Plan Schema (Pydantic)
# ---------------------------------------------------------------------------

class OperationTarget(BaseModel):
    type: str = Field(..., description="Target type: time_range, transcript_phrase, word_ids, selection, named_section")
    range: Optional[Dict[str, int]] = None
    phrase: Optional[str] = None
    word_ids: Optional[List[str]] = None
    section_name: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class OperationValidationInfo(BaseModel):
    valid: bool = True
    errors: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class PlannedOperation(BaseModel):
    operation_id: str = Field(..., description="Unique operation identifier within the plan")
    type: str = Field(..., description="Operation type matching engine Op: cut, keep, restore, speed, mute, bleep, clear_audio, add_caption, remove_caption, style_caption, move, add_text_overlay, remove_text_overlay")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Explicit parameters for the operation")
    target: Optional[OperationTarget] = None
    time_policy: Literal["source", "timeline"] = "source"
    validation: OperationValidationInfo = Field(default_factory=OperationValidationInfo)
    engine_op: Optional[Dict[str, Any]] = Field(None, description="The raw engine Op dict")


class ClarificationOptionSchema(BaseModel):
    label: str
    detail: Optional[str] = None
    operations: List[PlannedOperation] = Field(default_factory=list)


class ClarificationSchema(BaseModel):
    question: str
    options: List[ClarificationOptionSchema] = Field(default_factory=list)


class OperationPlan(BaseModel):
    instruction_id: str
    original_instruction: str
    status: Literal["APPLIED", "NEEDS_CLARIFICATION", "REJECTED"]
    operations: List[PlannedOperation] = Field(default_factory=list)
    clarification: Optional[ClarificationSchema] = None
    reason: Optional[str] = None
    confidence: float = 1.0
    created_at: int


# ---------------------------------------------------------------------------
# Interpret Context & Helper Types
# ---------------------------------------------------------------------------

@dataclass
class InterpretContext:
    transcript: Optional[Transcript]
    state: TimelineState
    selection: List[str] = field(default_factory=list)
    selected_caption_id: Optional[str] = None
    playhead_src_ms: Optional[int] = None
    silences: Optional[list] = None


@dataclass
class _Ctx:
    transcript: Optional[Transcript]
    state: TimelineState
    selection: List[str]
    selected_caption_id: Optional[str]
    playhead_src_ms: Optional[int]
    silences: Optional[list]
    words: List[Word]
    by_id: Dict[str, Word]
    dur: int
    tl_dur: float
    placed: List[PlacedSegment]

    def visible(self, w: Word) -> bool:
        return bool(place_word(self.placed, w).get("visible", False))


def _build_ctx(ic: InterpretContext) -> _Ctx:
    words = ic.transcript.words if ic.transcript else []
    placed = layout(ic.state.segments)
    return _Ctx(
        transcript=ic.transcript,
        state=ic.state,
        selection=list(ic.selection),
        selected_caption_id=ic.selected_caption_id,
        playhead_src_ms=ic.playhead_src_ms,
        silences=ic.silences,
        words=words,
        by_id={w.id: w for w in words},
        dur=ic.state.sourceDurationMs,
        tl_dur=timeline_duration(ic.state.segments),
        placed=placed,
    )


def _fmt_range(r: Interval) -> str:
    return f"{format_tc(r.start)}–{format_tc(r.end)}"


def _detect_basis(t: str) -> Tuple[str, Literal["source", "timeline"]]:
    """Check if the instruction specifies timeline time instead of default source time."""
    tl_re = re.compile(
        r"\s*\b(?:(?:in|of|on)\s+)?(?:the\s+)?(?:current|edited|output)(?:\s+(?:timeline|video|edit))?(?:\s+time)?\b|\s*\(?\btimeline time\)?|\s*\bin the timeline\b",
        re.IGNORECASE,
    )
    if tl_re.search(t):
        cleaned = tl_re.sub(" ", t)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned, "timeline"
    src_re = re.compile(r"\s*\(?\b(?:original|source)(?: source)?(?: time)?\)?", re.IGNORECASE)
    cleaned = src_re.sub(" ", t)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, "source"


def _clean_phrase(p: str) -> str:
    p = re.sub(r"^[\"\'“‘]|[\"\'”’]$", "", p)
    p = re.sub(r"^(?:the|a|an)\s+", "", p, flags=re.IGNORECASE)
    p = re.sub(r"\s+(?:section|part|segment|bit|sentence|mentions?|occurrences?)$", "", p, flags=re.IGNORECASE)
    return p.strip()


def _snap_ranges(c: _Ctx, ranges: List[Interval]) -> Tuple[List[Interval], List[str]]:
    if not c.words:
        return ranges, []
    notes: List[str] = []
    out: List[Interval] = []
    for r in ranges:
        snapped, moved = snap_to_words(c.words, r)
        notes.extend(moved)
        if snapped.end > snapped.start:
            out.append(snapped)
    return out, notes


def _selection_range(c: _Ctx) -> Optional[Interval]:
    ws = [c.by_id[wid] for wid in c.selection if wid in c.by_id]
    if not ws:
        return None
    return Interval(start=min(w.start for w in ws), end=max(w.end for w in ws))


def _sentence_range(c: _Ctx, start_idx: int, end_idx: int) -> Interval:
    if not c.words:
        return Interval(start=0, end=c.dur)
    a, _ = sentence_of(c.words, start_idx)
    _, b = sentence_of(c.words, end_idx)
    return Interval(start=c.words[a].start, end=c.words[b].end)


def _resolve_named_section(c: _Ctx, name: str) -> Optional[Interval]:
    """Resolve 'introduction', 'conclusion', 'opening', 'ending' from transcript or duration."""
    norm_name = norm_token(name)
    is_intro = norm_name in {"introduction", "intro", "opening", "beginning", "start"}
    is_conclusion = norm_name in {"conclusion", "conclude", "summary", "summarize", "ending", "end", "wrap up", "finally"}

    if is_intro:
        if c.words:
            # First check for explicit keyword match in words
            matches = search_phrase(c.words, "introduction") or search_phrase(c.words, "welcome")
            if matches:
                return _sentence_range(c, matches[0].first_index, matches[0].last_index)
            # Default to first sentence of transcript
            s_list = sentences(c.words)
            if s_list:
                a, b = s_list[0]
                return Interval(start=c.words[a].start, end=c.words[b].end)
        # If no transcript, assume initial portion (e.g. first 10s or 25% of video)
        return Interval(start=0, end=min(10000, c.dur))

    if is_conclusion:
        if c.words:
            matches = search_phrase(c.words, "conclusion") or search_phrase(c.words, "summary") or search_phrase(c.words, "finally")
            if matches:
                return _sentence_range(c, matches[-1].first_index, matches[-1].last_index)
            s_list = sentences(c.words)
            if s_list:
                a, b = s_list[-1]
                return Interval(start=c.words[a].start, end=c.words[b].end)
        end_start = max(0, c.dur - 10000)
        return Interval(start=end_start, end=c.dur)

    return None


def _op_to_planned(op: Op, op_id: str, time_policy: Literal["source", "timeline"] = "source", target: Optional[OperationTarget] = None, notes: Optional[List[str]] = None) -> PlannedOperation:
    op_type = op["type"]
    params: Dict[str, Any] = {}
    if "ranges" in op:
        params["ranges"] = [{"start": r.start, "end": r.end} for r in op["ranges"]]
    if "range" in op:
        params["range"] = {"start": op["range"].start, "end": op["range"].end}
    if "speed" in op:
        params["speed"] = op["speed"]
    if "wordIds" in op:
        params["wordIds"] = list(op["wordIds"])
    if "text" in op:
        params["text"] = op["text"]
    if "to" in op:
        params["to"] = op["to"]
    if "reason" in op:
        params["reason"] = op["reason"]

    raw_engine_op: Dict[str, Any] = dict(op)
    if "ranges" in raw_engine_op:
        raw_engine_op["ranges"] = [{"start": r.start, "end": r.end} for r in op["ranges"]]
    if "range" in raw_engine_op:
        raw_engine_op["range"] = {"start": op["range"].start, "end": op["range"].end}

    return PlannedOperation(
        operation_id=op_id,
        type=op_type,
        parameters=params,
        target=target,
        time_policy=time_policy,
        validation=OperationValidationInfo(valid=True, notes=notes or []),
        engine_op=raw_engine_op,
    )


# ---------------------------------------------------------------------------
# Core Translation Logic
# ---------------------------------------------------------------------------

def _ops_result(ops_list: List[Op], notes: Optional[List[str]] = None, planned: Optional[List[PlannedOperation]] = None) -> Dict[str, Any]:
    return {
        "kind": "ops",
        "ops": ops_list,
        "notes": notes or [],
        "planned_operations": planned or [],
    }


def _reject_result(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "kind": "reject",
        "code": code,
        "message": message,
        "details": details or {},
    }


def _clarify_result(question: str, options: List[ClarificationOption], notes: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "kind": "clarify",
        "question": question,
        "options": [
            {
                "label": o.label,
                "detail": o.detail,
                "ops": o.ops,
            }
            for o in options
        ],
        "notes": notes or [],
    }


def _with_range(
    c: _Ctx,
    a: int,
    b: int,
    basis: Literal["source", "timeline"],
    build_fn: Any,
) -> Dict[str, Any]:
    limit = c.dur if basis == "source" else int(c.tl_dur)
    label = "original source time" if basis == "source" else "current timeline time"
    if b <= a:
        return _reject_result("INVALID_RANGE", f"The range {format_tc(a)}–{format_tc(b)} ends before it starts.")
    if a >= limit:
        return _reject_result(
            "RANGE_OUT_OF_BOUNDS",
            f"{format_tc(a)} is beyond the end of the video ({format_tc(limit)} in {label}).",
            {"start": a, "limit": limit},
        )
    if b > limit:
        # Range extends past limit -> clarify or clamp
        end = limit
        ranges = [Interval(start=a, end=end)] if basis == "source" else tl_range_to_src(c.placed, a, end)
        inner = build_fn(ranges, [f"End clamped from {format_tc(b)} to the video end {format_tc(limit)}."])
        if inner.get("kind") != "ops":
            return inner
        return _clarify_result(
            question=f"{format_tc(b)} is past the end of the video ({format_tc(limit)} in {label}). Use the video end instead?",
            options=[
                ClarificationOption(
                    label=f"Use {format_tc(a)}–{format_tc(limit)}",
                    detail="Clamp the range to the video end",
                    ops=inner["ops"],
                )
            ],
            notes=inner.get("notes", []),
        )

    ranges = [Interval(start=a, end=b)] if basis == "source" else tl_range_to_src(c.placed, a, b)
    notes = [
        f"Interpreted {format_tc(a)}–{format_tc(b)} as {label} (time policy)."
    ]
    if basis == "timeline":
        notes.append(f"That maps to source {', '.join(_fmt_range(r) for r in ranges)}.")
    return build_fn(ranges, notes)


def _target_phrase(
    c: _Ctx,
    phrase: str,
    unit: Literal["word", "sentence"],
    verb: str,
    build_fn: Any,
) -> Dict[str, Any]:
    if not c.transcript or not c.words:
        return _reject_result("MISSING_TRANSCRIPT", f"A transcript is required to locate “{phrase}”.")

    all_matches = [m for m in search_phrase(c.words, phrase) if all(c.visible(c.by_id[wid]) for wid in m.word_ids)]

    def range_of(m: Any) -> Interval:
        return _sentence_range(c, m.first_index, m.last_index) if unit == "sentence" else Interval(start=m.start, end=m.end)

    def desc_of(m: Any) -> str:
        r = range_of(m)
        return f"source {_fmt_range(r)} — {context_of(c.words, m.first_index, m.last_index)}"

    if not all_matches:
        related = [m for m in search_phrase(c.words, phrase, include_related=True) if m.kind == "related" and all(c.visible(c.by_id[wid]) for wid in m.word_ids)]
        if related:
            return _clarify_result(
                question=f"No exact mention of “{phrase}”. I found {len(related)} related mention(s). Which one should I {verb}?",
                options=[
                    ClarificationOption(
                        label=f"Related {i + 1}: “{m.matched}”",
                        detail=desc_of(m),
                        ops=build_fn(range_of(m)),
                    )
                    for i, m in enumerate(related)
                ],
                notes=[f"Related terms searched: {', '.join(related_terms(phrase))}."],
            )
        return _reject_result("NOT_FOUND", f"I couldn't find “{phrase}” in the current timeline.")

    if len(all_matches) == 1:
        m = all_matches[0]
        r = range_of(m)
        return _ops_result(build_fn(r), [f"Matched “{phrase}” once: {desc_of(m)}."])

    # Multiple matches -> clarification required for destructive edits
    options = [
        ClarificationOption(
            label=f"Match {i + 1}",
            detail=desc_of(m),
            ops=build_fn(range_of(m)),
        )
        for i, m in enumerate(all_matches)
    ]
    # Also add "All N matches"
    all_ops: List[Op] = []
    for m in all_matches:
        all_ops.extend(build_fn(range_of(m)))
    options.append(
        ClarificationOption(
            label=f"All {len(all_matches)} matches",
            detail=f"{verb} every match",
            ops=all_ops,
        )
    )
    return _clarify_result(
        question=f"“{phrase}” is mentioned {len(all_matches)} times. Which one should I {verb}?",
        options=options,
        notes=["No change was made yet: destructive edits on ambiguous targets need your choice."],
    )


# ---------------------------------------------------------------------------
# Single Instruction Interpreter
# ---------------------------------------------------------------------------

def _interpret_single(c: _Ctx, raw_input: str) -> Dict[str, Any]:
    orig = raw_input.strip().rstrip(".!").strip()
    lower0 = re.sub(r"\s+", " ", orig.lower().replace("’", "'"))
    t, basis = _detect_basis(lower0)

    # 1. Undo / Redo / Restore revision
    if re.match(r"^(?:undo|undo (?:the |that |my )?(?:last|previous)?\s*(?:operation|edit|change|step)?)$", t):
        return {"kind": "undo"}
    if re.match(r"^(?:redo|redo (?:the |that )?(?:last|previous)?\s*(?:operation|edit|change|step)?)$", t):
        return {"kind": "redo"}
    rev_m = re.match(r"^(?:restore|revert(?: back)? to|go back to|roll back to|return to)\s+(?:the\s+)?(?:revision|rev|version|r)\s*#?\s*(\d+)$", t)
    if rev_m:
        return {"kind": "restore_revision", "revisionId": f"r{rev_m.group(1)}"}

    # 2. Add captions
    if re.match(r"^add (?:a )?captions?$", t) or t == "add captions":
        if not c.transcript or not c.words:
            return _reject_result(
                "MISSING_TRANSCRIPT",
                "A word-level transcript is required to add captions. Please upload or generate a transcript first.",
            )
        # Generate sentence-based or phrase-based captions
        s_list = sentences(c.words)
        ops_list: List[Op] = []
        for a, b in s_list:
            chunk_words = c.words[a : b + 1]
            if not chunk_words:
                continue
            text = " ".join(w.text for w in chunk_words)
            w_ids = [w.id for w in chunk_words]
            ops_list.append({"type": "add_caption", "text": text, "wordIds": w_ids})
        if not ops_list:
            return _reject_result("NO_CONTENT", "No words found in transcript to generate captions.")
        return _ops_result(ops_list, [f"Generated {len(ops_list)} caption(s) from transcript sentences."])

    cap_quote = re.search(r'caption\s+(?:saying\s+|that says\s+)?["“\'‘](.+?)["”\'’](?=\s|$)', orig, re.IGNORECASE)
    if re.match(r"^add (?:a )?caption\b", t):
        if not cap_quote:
            return _reject_result("INVALID_CAPTION", 'Put the caption text in quotes, e.g. add caption "Welcome!" to the selected words.')
        cap_text = cap_quote.group(1).strip()
        rest = t[t.find(cap_quote.group(1).lower()) + len(cap_quote.group(1)) + 1 :]
        rest = re.sub(r'^["”\'’\s]+', "", rest)
        target_str = re.sub(r"^(?:to|on|for|at|over)\s+(?:the\s+)?(?:words?\s+)?(?:spoken\s+)?(?:at\s+)?", "", rest).strip()

        if not target_str:
            if c.selection:
                return _ops_result([{"type": "add_caption", "text": cap_text, "wordIds": list(c.selection)}], [f"Anchored to the {len(c.selection)} selected word(s)."])
            return _clarify_result(
                question=f"Which words should “{cap_text}” attach to? Select words in the transcript or specify a time range.",
                options=[],
            )

        # Target can be time range
        rm = RANGE_RE.search(target_str)
        if rm:
            t1 = parse_time(rm.group(1))
            t2 = parse_time(rm.group(2))
            if t1 is not None and t2 is not None and t2 > t1:
                ws = words_in_range(c.words, Interval(t1, t2))
                if not ws:
                    return _reject_result("NOT_FOUND", f"No words spoken between {format_tc(t1)} and {format_tc(t2)}.")
                return _ops_result([{"type": "add_caption", "text": cap_text, "wordIds": [w.id for w in ws]}], [f"Anchored to {len(ws)} word(s) in source range."])

        # Target can be phrase
        cleaned_target = _clean_phrase(target_str)
        matches = search_phrase(c.words, cleaned_target)
        if matches:
            return _ops_result([{"type": "add_caption", "text": cap_text, "wordIds": list(matches[0].word_ids)}], [f"Anchored to “{cleaned_target}”."])
        return _reject_result("NOT_FOUND", f"Could not find target words for “{cleaned_target}”.")

    # 3. Caption styling & removal
    if re.search(r"\b(?:bigger|larger|smaller|to the top|to the bottom|at the top|at the bottom)\b", t) and re.search(r"\b(?:it|caption|captions|text|subtitle)\b", t):
        size_step = 1 if re.search(r"bigger|larger", t) else (-1 if re.search(r"smaller", t) else None)
        pos = "top" if "top" in t else ("bottom" if "bottom" in t else None)
        if c.selected_caption_id:
            op: Op = {"type": "style_caption", "captionId": c.selected_caption_id, "sizeStep": size_step, "position": pos}
            return _ops_result([op], [f"Applied style to caption {c.selected_caption_id}."])
        if not c.state.captions:
            return _reject_result("NO_CAPTIONS", "There are no captions yet. Add a caption first.")
        options = [
            ClarificationOption(
                label=f"“{cap.text}”",
                detail=f"{cap.id} · {len(cap.wordIds)} anchored words",
                ops=[{"type": "style_caption", "captionId": cap.id, "sizeStep": size_step, "position": pos}],
            )
            for cap in c.state.captions
        ]
        return _clarify_result("Which caption do you mean?", options)

    if re.search(r"^(?:remove|delete) (?:the |this |that )?(?:selected )?caption\b", t):
        if c.selected_caption_id:
            return _ops_result([{"type": "remove_caption", "captionId": c.selected_caption_id}])
        if not c.state.captions:
            return _reject_result("NO_CAPTIONS", "There are no captions to remove.")
        options = [
            ClarificationOption(label=f"“{cap.text}”", detail=cap.id, ops=[{"type": "remove_caption", "captionId": cap.id}])
            for cap in c.state.captions
        ]
        return _clarify_result("Which caption should I remove?", options)

    # 4. Content restore
    rest_m = re.match(r"^(?:restore|bring back|recover|put back|uncut)\s+(.+)$", t)
    if rest_m:
        target = rest_m.group(1)
        target = re.sub(r"^(?:the\s+)?(?:earlier|original|removed|cut)\s+", "", target)
        target = re.sub(r"^the\s+", "", target)

        if re.match(r"^(?:everything|all|all cuts|whole video|entire video)$", target):
            return _ops_result([{"type": "restore", "ranges": [Interval(0, c.dur)], "reason": "restore_all"}], ["Restoring all cut content."])

        if re.match(r"^(?:intro|introduction|opening|beginning|start)$", target):
            first_kept = present_intervals(c.state.segments)[0].start if present_intervals(c.state.segments) else c.dur
            if first_kept <= 0:
                return _reject_result("NO_CHANGE", "The opening of the video is not cut, so there is nothing to restore.")
            return _ops_result([{"type": "restore", "ranges": [Interval(0, first_kept)], "reason": "restore_intro"}], [f"Restoring introduction from 0 to {format_tc(first_kept)}."])

        first_m = re.match(r"^first\s+" + TIME_PATTERN, target)
        if first_m:
            n = parse_time(first_m.group(1))
            if n is not None:
                return _with_range(c, 0, n, basis, lambda ranges, notes: _ops_result([{"type": "restore", "ranges": ranges}], notes))

        rm = RANGE_RE.search(target)
        if rm:
            t1 = parse_time(rm.group(1))
            t2 = parse_time(rm.group(2))
            if t1 is not None and t2 is not None:
                return _with_range(c, t1, t2, basis, lambda ranges, notes: _ops_result([{"type": "restore", "ranges": ranges}], notes))

        phrase = _clean_phrase(re.sub(r"^(?:the )?(?:part|section|sentence) (?:about|with|on)\s+", "", target))
        if not c.words:
            return _reject_result("MISSING_TRANSCRIPT", "Transcript required to identify cut content by phrase.")
        cut_matches = [m for m in search_phrase(c.words, phrase) if not all(c.visible(c.by_id[wid]) for wid in m.word_ids)]
        if not cut_matches:
            return _reject_result("NOT_FOUND", f"No cut content mentions “{phrase}”.")
        if len(cut_matches) == 1:
            r = _sentence_range(c, cut_matches[0].first_index, cut_matches[0].last_index)
            return _ops_result([{"type": "restore", "ranges": [r]}])
        options = [
            ClarificationOption(
                label=f"Match {i + 1}",
                detail=f"source {_fmt_range(_sentence_range(c, m.first_index, m.last_index))} — {context_of(c.words, m.first_index, m.last_index)}",
                ops=[{"type": "restore", "ranges": [_sentence_range(c, m.first_index, m.last_index)]}],
            )
            for i, m in enumerate(cut_matches)
        ]
        return _clarify_result(f"“{phrase}” appears in {len(cut_matches)} cut sentences. Which should I restore?", options)

    # 5. Fillers
    if re.search(r"\b(?:remove|cut|delete|clean up|strip|drop)\b.*\b(?:filler(?:s| words?)?|ums?(?: and uhs?)?|uhs?)\b", t):
        if not c.words:
            return _reject_result("MISSING_TRANSCRIPT", "A transcript is required to identify filler words.")
        idx = [i for i, w in enumerate(c.words) if is_filler(c.words, i) and c.visible(w)]
        if not idx:
            return _reject_result("NO_CHANGE", "No filler words remain in the current timeline.")
        ranges = [Interval(start=c.words[i].start, end=c.words[i].end) for i in idx]
        return _ops_result([{"type": "cut", "ranges": ranges, "reason": "filler_words"}], [f"Removing {len(idx)} filler word(s)."])

    # 6. Silences
    if re.search(r"\b(?:remove|cut|trim|delete|shorten)\b.*\b(?:silences?|pauses?|dead air|gaps?)\b", t):
        th_m = re.search(r"(?:longer than|over|above|more than|exceeding|>)\s*" + TIME_PATTERN, t)
        thr = parse_time(th_m.group(1)) if th_m else None

        def build_silence_cut(threshold: int) -> Tuple[List[Op], List[str]]:
            present = present_intervals(c.state.segments)
            vis = [w for w in c.words if c.visible(w)]
            bounds = [{"end": 0}] + [{"start": w.start, "end": w.end} for w in vis] + [{"start": c.dur}]
            cuts: List[Interval] = []
            for i in range(len(bounds) - 1):
                a = bounds[i]["end"]
                b = bounds[i + 1]["start"]
                gap = b - a
                if gap > threshold:
                    pad = 250
                    r_start = a if a == 0 else a + pad
                    r_end = b if b == c.dur else b - pad
                    for p in present:
                        s = max(p.start, r_start)
                        e = min(p.end, r_end)
                        if e - s > threshold / 2:
                            cuts.append(Interval(start=s, end=e))
            if not cuts:
                return [], []
            return [{"type": "cut", "ranges": cuts, "reason": "silence"}], [f"Removing {len(cuts)} silence(s) longer than {threshold / 1000}s."]

        if thr is None:
            # Clarify options
            options: List[ClarificationOption] = []
            for candidate in (1000, 2000, 3000):
                c_ops, notes = build_silence_cut(candidate)
                if c_ops:
                    options.append(ClarificationOption(label=f"Longer than {candidate / 1000:g}s", detail=notes[0], ops=c_ops))
            if not options:
                return _reject_result("NO_CHANGE", "There are no significant silences to remove.")
            return _clarify_result("How long must a silence be before removing it?", options)

        silence_ops, silence_notes = build_silence_cut(thr)
        if not silence_ops:
            return _reject_result("NO_CHANGE", f"No removable silences longer than {thr / 1000:g}s.")
        return _ops_result(silence_ops, silence_notes)

    # 7. Speed
    factor_m = re.search(r"(\d+(?:\.\d+)?)\s*x\b", t) or re.search(r"\bx\s*(\d+(?:\.\d+)?)\b", t)
    named_speed = 2.0 if re.search(r"\b(?:double speed|twice as fast)\b", t) else (0.5 if re.search(r"\bhalf speed\b", t) else None)
    if (factor_m or named_speed) and re.search(r"\b(?:speed|play|fast|slow|make|set)\b", t):
        sp_val = named_speed if named_speed is not None else float(factor_m.group(1))  # type: ignore[union-attr]
        if not (MIN_SPEED <= sp_val <= MAX_SPEED):
            return _reject_result("INVALID_SPEED", f"Speed must be between {MIN_SPEED}x and {MAX_SPEED}x (got {sp_val}x).")

        def speed_build(ranges: List[Interval], notes: List[str]) -> Dict[str, Any]:
            ops_out: List[Op] = [{"type": "speed", "range": r, "speed": sp_val} for r in ranges]
            return _ops_result(ops_out, notes)

        # Check for named section like "introduction"
        named_sec_m = re.search(r"\b(?:the\s+)?(introduction|intro|beginning|opening|conclusion|ending)\b", t)
        if named_sec_m:
            sec_range = _resolve_named_section(c, named_sec_m.group(1))
            if sec_range:
                return speed_build([sec_range], [f"Speeding up {named_sec_m.group(1)} to {sp_val}x."])

        after_m = re.search(r"\bafter\s+" + TIME_PATTERN, t)
        if after_m:
            a = parse_time(after_m.group(1))
            if a is not None:
                limit = c.dur if basis == "source" else int(c.tl_dur)
                return _with_range(c, a, limit, basis, speed_build)

        before_m = re.search(r"\bbefore\s+" + TIME_PATTERN, t)
        if before_m:
            b = parse_time(before_m.group(1))
            if b is not None:
                return _with_range(c, 0, b, basis, speed_build)

        rm = RANGE_RE.search(t)
        if rm:
            t1 = parse_time(rm.group(1))
            t2 = parse_time(rm.group(2))
            if t1 is not None and t2 is not None:
                return _with_range(c, t1, t2, basis, speed_build)

        if re.search(r"\b(?:whole|entire|everything|all of it)\b", t):
            return speed_build([Interval(start=0, end=c.dur)], ["Applied speed change to the whole video."])

        sel = _selection_range(c)
        if sel:
            return speed_build([sel], [f"Speeding up selected range {_fmt_range(sel)}."])

        return _clarify_result(
            f"Which section should play at {sp_val}x? Select words or give a time range.",
            options=[ClarificationOption(label="Whole video", ops=[{"type": "speed", "range": Interval(0, c.dur), "speed": sp_val}])],
        )

    # 8. Mute / Bleep
    mb_m = re.match(r"^(mute|bleep|silence|censor|unmute|unbleep)\s+(.+)$", t)
    if mb_m:
        action = mb_m.group(1)
        target_str = mb_m.group(2).strip()
        op_type: Literal["mute", "bleep", "clear_audio"] = "clear_audio" if action in ("unmute", "unbleep") else ("bleep" if action in ("bleep", "censor") else "mute")

        if re.match(r"^(?:the )?(?:selected(?: words?| section| text)?|selection|it|this)$", target_str):
            if not c.selection:
                return _clarify_result("Which words? Select words in the transcript first.", [])
            return _ops_result([{"type": op_type, "wordIds": list(c.selection)}], [f"Applied {op_type} to {len(c.selection)} selected word(s)."])

        if not c.words:
            return _reject_result("MISSING_TRANSCRIPT", f"A transcript is required to {action} words.")

        phrase = _clean_phrase(re.sub(r"^(?:the )?(?:word|phrase|words)\s+", "", target_str))
        matches = search_phrase(c.words, phrase)
        if not matches:
            return _reject_result("NOT_FOUND", f"“{phrase}” does not appear in the transcript.")
        word_ids = [wid for m in matches for wid in m.word_ids]
        return _ops_result([{"type": op_type, "wordIds": word_ids}], [f"Matched “{phrase}” {len(matches)} time(s); applied {op_type}."])

    # 9. Move / Reorder
    mv_m = re.match(r"^(?:move|reorder|put|place)\s+(.+?)\s+(to (?:the )?(?:start|beginning|end)|(?:before|after)\s+.+)$", t)
    if mv_m:
        what = mv_m.group(1).strip()
        what = re.sub(r"^the\s+", "", what)
        dest = mv_m.group(2).strip()

        # Target range
        mv_range: Optional[Interval] = None
        notes: List[str] = []

        if re.match(r"^(?:selected(?: section| words?| part)?|selection|it|this)$", what):
            mv_range = _selection_range(c)
            if not mv_range:
                return _clarify_result("Which section should I move? Select words in the transcript.", [])
            notes.append(f"Moving selection {_fmt_range(mv_range)}.")
        elif what in ("conclusion", "ending", "summary"):
            mv_range = _resolve_named_section(c, what)
            if not mv_range:
                return _reject_result("NOT_FOUND", "Could not locate conclusion section.")
            notes.append(f"Moving conclusion, source {_fmt_range(mv_range)}.")
        elif what in ("intro", "introduction", "opening", "beginning"):
            mv_range = _resolve_named_section(c, what)
            if not mv_range:
                return _reject_result("NOT_FOUND", "Could not locate introduction section.")
            notes.append(f"Moving introduction, source {_fmt_range(mv_range)}.")
        else:
            rm = RANGE_RE.search(what)
            if rm:
                t1 = parse_time(rm.group(1))
                t2 = parse_time(rm.group(2))
                if t1 is not None and t2 is not None and t2 > t1 and t1 < c.dur:
                    mv_range = Interval(start=t1, end=min(t2, c.dur))
                    notes.append(f"Moving range {_fmt_range(mv_range)}.")
            else:
                phrase = _clean_phrase(re.sub(r"^(?:sentence|part|section) (?:about|with)\s+", "", what))
                if not c.words:
                    return _reject_result("MISSING_TRANSCRIPT", "Transcript required to identify section to move.")
                ms = [m for m in search_phrase(c.words, phrase) if all(c.visible(c.by_id[wid]) for wid in m.word_ids)]
                if not ms:
                    return _reject_result("NOT_FOUND", f"I couldn't find “{phrase}” to move.")
                if len(ms) > 1:
                    return _reject_result("AMBIGUOUS_TARGET", f"“{phrase}” appears {len(ms)} times. Select it in transcript to move.")
                mv_range = _sentence_range(c, ms[0].first_index, ms[0].last_index)
                notes.append(f"Moving sentence containing “{phrase}”, source {_fmt_range(mv_range)}.")

        if not mv_range:
            return _reject_result("INVALID_TARGET", "Could not resolve section to move.")

        # Destination
        to_dest: Union[Literal["start", "end"], Dict[str, int]]
        if re.search(r"start|beginning", dest):
            to_dest = "start"
        elif re.search(r"end$", dest):
            to_dest = "end"
        else:
            dm = re.match(r"^(before|after)\s+(.+)$", dest)
            if not dm:
                return _reject_result("INVALID_DESTINATION", f"Unknown destination '{dest}'.")
            is_after = dm.group(1) == "after"
            dest_target = dm.group(2).strip()

            dest_range: Optional[Interval] = None
            if dest_target in ("intro", "introduction", "opening", "beginning", "the introduction", "the intro"):
                dest_range = _resolve_named_section(c, "introduction")
            elif dest_target in ("conclusion", "ending", "summary", "the conclusion"):
                dest_range = _resolve_named_section(c, "conclusion")
            else:
                time_m = re.match(r"^" + TIME_PATTERN + r"$", dest_target)
                if time_m:
                    tm_val = parse_time(time_m.group(1))
                    if tm_val is not None:
                        to_dest = {"beforeSrc": tm_val}
                        return _ops_result([{"type": "move", "range": mv_range, "to": to_dest}], notes)
                phrase = _clean_phrase(dest_target)
                if c.words:
                    ms = [m for m in search_phrase(c.words, phrase) if all(c.visible(c.by_id[wid]) for wid in m.word_ids)]
                    if len(ms) == 1:
                        dest_range = _sentence_range(c, ms[0].first_index, ms[0].last_index)

            if not dest_range:
                return _reject_result("NOT_FOUND", f"Could not resolve destination '{dest_target}'.")

            dest_time = dest_range.end if is_after else dest_range.start
            to_dest = {"beforeSrc": dest_time}

        return _ops_result([{"type": "move", "range": mv_range, "to": to_dest}], notes)

    # 10. Cuts
    is_cut = bool(re.match(r"^(?:cut|remove|delete|trim|drop|lose|get rid of)\b", t))
    if is_cut:
        body = re.sub(r"^(?:cut|remove|delete|trim|drop|lose|get rid of)\s+(?:out\s+)?", "", t)

        def cut_build(ranges: List[Interval], notes: List[str]) -> Dict[str, Any]:
            s_ranges, s_notes = _snap_ranges(c, ranges)
            if not s_ranges:
                return _reject_result("INVALID_RANGE", "After word-snapping, the range is empty.")
            return _ops_result([{"type": "cut", "ranges": s_ranges}], notes + s_notes)

        if re.match(r"^(?:the )?(?:selected(?: words?| section| part| text)?|selection)$", body):
            sel = _selection_range(c)
            if not sel:
                return _clarify_result("Nothing is selected. Select words in the transcript to cut.", [])
            return _ops_result([{"type": "cut", "ranges": [sel]}], [f"Cutting selection {_fmt_range(sel)}."])

        if re.match(r"^(?:the )?(?:whole|entire|full) (?:video|thing|clip|recording)$|^(?:everything|all of it|all)$", body):
            return _ops_result([{"type": "cut", "ranges": [Interval(0, c.dur)]}])

        first_m = re.match(r"^(?:the )?first\s+" + TIME_PATTERN, body)
        if first_m:
            n = parse_time(first_m.group(1))
            if n is not None:
                return _with_range(c, 0, n, basis, cut_build)

        last_m = re.match(r"^(?:the )?last\s+" + TIME_PATTERN, body)
        if last_m:
            n = parse_time(last_m.group(1))
            if n is not None:
                limit = c.dur if basis == "source" else int(c.tl_dur)
                return _with_range(c, max(0, limit - n), limit, basis, cut_build)

        ba_m = re.match(r"^everything\s+(before|after)\s+(.+)$", body)
        if ba_m:
            is_before = ba_m.group(1) == "before"
            target_expr = ba_m.group(2).strip()
            tm = re.match(r"^" + TIME_PATTERN + r"$", target_expr)
            if tm:
                x = parse_time(tm.group(1))
                if x is not None:
                    limit = c.dur if basis == "source" else int(c.tl_dur)
                    return _with_range(c, 0, x, basis, cut_build) if is_before else _with_range(c, x, limit, basis, cut_build)

            # Check if target is 'introduction' or named section
            if target_expr in ("intro", "introduction", "the introduction", "the intro"):
                intro_range = _resolve_named_section(c, "introduction")
                if intro_range:
                    cut_r = Interval(0, intro_range.start) if is_before else Interval(intro_range.end, c.dur)
                    if cut_r.end <= cut_r.start:
                        return _reject_result("NO_CHANGE", "There is no content to cut.")
                    return _ops_result([{"type": "cut", "ranges": [cut_r]}], [f"Cut everything {'before' if is_before else 'after'} introduction."])

            phrase = _clean_phrase(target_expr)
            return _target_phrase(
                c, phrase, "sentence", "cut before" if is_before else "cut after",
                lambda r: [{"type": "cut", "ranges": [Interval(0, r.start) if is_before else Interval(r.end, c.dur)]}],
            )

        rm = RANGE_RE.search(body)
        if rm:
            t1 = parse_time(rm.group(1))
            t2 = parse_time(rm.group(2))
            if t1 is not None and t2 is not None:
                return _with_range(c, t1, t2, basis, cut_build)

        # Phrase-based cuts
        about_m = re.match(r"^(?:the )?(?:part|section|bit|sentence|segment)s?\s+(?:about|where .+? (?:talks?|mentions?|says?)|on|with|mentioning)\s+(.+)$", body)
        sect_m = re.match(r"^(?:the )?(.+?)\s+(?:section|part|segment|bit|sentence)$", body)
        word_m = re.match(r"^(?:the |every |all )?(?:word|phrase|mentions? of|occurrences? of)\s+(.+)$", body)
        if about_m or sect_m:
            target_p = _clean_phrase((about_m or sect_m).group(1))  # type: ignore[union-attr]
            return _target_phrase(c, target_p, "sentence", "cut", lambda r: [{"type": "cut", "ranges": [r]}])
        if word_m:
            target_p = _clean_phrase(word_m.group(1))
            return _target_phrase(c, target_p, "word", "cut", lambda r: [{"type": "cut", "ranges": [r]}])

        phrase = _clean_phrase(body)
        if phrase and len(phrase.split()) <= 6:
            return _target_phrase(c, phrase, "word", "cut", lambda r: [{"type": "cut", "ranges": [r]}])

    # 11. Keep Operations
    is_keep = bool(re.match(r"^(?:keep\s+only|only\s+keep|keep|save|preserve)\b", t))
    if is_keep:
        body = re.sub(r"^(?:keep\s+only|only\s+keep|keep|save|preserve)\s+", "", t)
        body = re.sub(r"^only\s+", "", body)

        def keep_build(ranges: List[Interval], notes: List[str]) -> Dict[str, Any]:
            s_ranges, s_notes = _snap_ranges(c, ranges)
            if not s_ranges:
                return _reject_result("INVALID_RANGE", "After word-snapping, the range is empty.")
            return _ops_result([{"type": "keep", "ranges": s_ranges}], notes + s_notes)

        # "Keep only the section where the speaker explains machine learning"
        # "Keep the part about machine learning"
        about_m = re.match(r"^(?:the )?(?:part|section|bit|sentence|segment)s?\s+(?:about|where .+? (?:talks?|mentions?|says?|explains?)|on|with|mentioning)\s+(.+)$", body)
        sect_m = re.match(r"^(?:the )?(.+?)\s+(?:section|part|segment|bit|sentence)$", body)
        if about_m or sect_m:
            target_p = _clean_phrase((about_m or sect_m).group(1))  # type: ignore[union-attr]
            return _target_phrase(c, target_p, "sentence", "keep", lambda r: [{"type": "keep", "ranges": [r]}])

        if body in ("intro", "introduction", "the introduction", "the intro", "opening", "beginning"):
            intro_r = _resolve_named_section(c, "introduction")
            if intro_r:
                return _ops_result([{"type": "keep", "ranges": [intro_r]}], ["Keeping only the introduction."])

        first_m = re.match(r"^(?:the )?first\s+" + TIME_PATTERN, body)
        if first_m:
            n = parse_time(first_m.group(1))
            if n is not None:
                return _with_range(c, 0, n, basis, keep_build)

        rm = RANGE_RE.search(body)
        if rm:
            t1 = parse_time(rm.group(1))
            t2 = parse_time(rm.group(2))
            if t1 is not None and t2 is not None:
                return _with_range(c, t1, t2, basis, keep_build)

        phrase = _clean_phrase(body)
        if phrase and len(phrase.split()) <= 6:
            return _target_phrase(c, phrase, "sentence", "keep", lambda r: [{"type": "keep", "ranges": [r]}])

    # 12. Search (non-destructive)
    find_m = re.match(r"^(?:find|search(?: for)?|show(?: me)?|locate|where (?:is|do (?:i|we) (?:say|mention)))\s+(?:all\s+)?(?:mentions of\s+|the word\s+)?(.+?)\??$", t)
    if find_m:
        phrase = _clean_phrase(find_m.group(1))
        if not c.words:
            return _reject_result("MISSING_TRANSCRIPT", f"Transcript required to search for “{phrase}”.")
        ms = search_phrase(c.words, phrase, include_related=True)
        if not ms:
            return _reject_result("NOT_FOUND", f"No mention of “{phrase}” or related terms.")
        options = [
            ClarificationOption(
                label=f"{'Match' if m.kind == 'exact' else 'Related'} {i + 1}: “{m.matched}”",
                detail=f"source {_fmt_range(_sentence_range(c, m.first_index, m.last_index))} — {context_of(c.words, m.first_index, m.last_index)}",
                ops=[{"type": "cut", "ranges": [_sentence_range(c, m.first_index, m.last_index)]}],
            )
            for i, m in enumerate(ms)
        ]
        return _clarify_result(
            question=f"Found {len([m for m in ms if m.kind == 'exact'])} exact and {len([m for m in ms if m.kind == 'related'])} related mention(s) of “{phrase}”. Cut one?",
            options=options,
        )

    return _reject_result(
        "UNSUPPORTED_INSTRUCTION",
        f"I cannot map “{raw_input}” to a supported editing operation. Examples of supported edits: Remove the first 10 seconds, Cut the section from 00:30 to 00:45, Speed up the introduction to 1.5x, Move the conclusion before the introduction, Add captions.",
    )


# ---------------------------------------------------------------------------
# Multi-Part Instruction Splitting & Public API
# ---------------------------------------------------------------------------

def split_parts(input_str: str) -> List[str]:
    """Split multi-part instructions outside quotes."""
    quotes: List[str] = []

    def mask(m: Any) -> str:
        quotes.append(m.group(0))
        return f"\x00{len(quotes) - 1}\x00"

    masked = re.sub(r'["“][^"”]*["”]', mask, input_str)
    # Split on semicolons or conjunctions preceding supported action verbs
    pattern = r"\s*;\s*|\s*,?\s+(?:and then|then)\s+|\s*,?\s+and\s+(?=(?:remove|cut|delete|mute|bleep|speed|slow|make|add|move|restore|undo|redo|trim|keep)\b)"
    parts = re.split(pattern, masked, flags=re.IGNORECASE)
    out: List[str] = []
    for p in parts:
        restored = re.sub(r"\x00(\d+)\x00", lambda m: quotes[int(m.group(1))], p).strip()
        if restored:
            out.append(restored)
    return out


def interpret(input_str: str, ctx: InterpretContext) -> Dict[str, Any]:
    """Interpret natural-language user instruction and return an interpretation result.
    Matches the executor interface: returns dict with 'kind' in ('ops', 'clarify', 'reject', 'undo', 'redo', 'restore_revision').
    Also attaches structured 'plan' (OperationPlan) to the returned dictionary.
    """
    now = int(time.time() * 1000)
    instruction_id = f"ins_{uuid.uuid4().hex[:8]}"

    if not input_str or not input_str.strip():
        plan = OperationPlan(
            instruction_id=instruction_id,
            original_instruction=input_str or "",
            status="REJECTED",
            operations=[],
            reason="Instruction cannot be empty.",
            confidence=0.0,
            created_at=now,
        )
        return {
            "kind": "reject",
            "code": "EMPTY_INSTRUCTION",
            "message": "Type an editing instruction first.",
            "details": {},
            "plan": plan,
        }

    c = _build_ctx(ctx)
    parts = split_parts(input_str)

    if not parts:
        plan = OperationPlan(
            instruction_id=instruction_id,
            original_instruction=input_str,
            status="REJECTED",
            operations=[],
            reason="Type an editing instruction.",
            confidence=0.0,
            created_at=now,
        )
        return {
            "kind": "reject",
            "code": "EMPTY_INSTRUCTION",
            "message": "Type an editing instruction.",
            "details": {},
            "plan": plan,
        }

    if len(parts) == 1:
        res = _interpret_single(c, parts[0])
        kind = res["kind"]
        if kind == "reject":
            plan = OperationPlan(
                instruction_id=instruction_id,
                original_instruction=input_str,
                status="REJECTED",
                operations=[],
                reason=res["message"],
                confidence=0.0,
                created_at=now,
            )
            return {**res, "plan": plan}

        if kind == "clarify":
            clar_options = [
                ClarificationOptionSchema(
                    label=o["label"],
                    detail=o.get("detail"),
                    operations=[
                        _op_to_planned(op, f"op_{idx + 1}")
                        for idx, op in enumerate(o.get("ops", []))
                    ],
                )
                for o in res["options"]
            ]
            plan = OperationPlan(
                instruction_id=instruction_id,
                original_instruction=input_str,
                status="NEEDS_CLARIFICATION",
                operations=[],
                clarification=ClarificationSchema(question=res["question"], options=clar_options),
                reason=res["question"],
                confidence=0.5,
                created_at=now,
            )
            return {**res, "plan": plan}

        if kind in ("undo", "redo", "restore_revision"):
            plan = OperationPlan(
                instruction_id=instruction_id,
                original_instruction=input_str,
                status="APPLIED",
                operations=[],
                confidence=1.0,
                created_at=now,
            )
            return {**res, "plan": plan}

        # kind == "ops"
        ops_list = res["ops"]
        planned_ops = [
            _op_to_planned(op, f"op_{idx + 1}", notes=res.get("notes"))
            for idx, op in enumerate(ops_list)
        ]
        plan = OperationPlan(
            instruction_id=instruction_id,
            original_instruction=input_str,
            status="APPLIED",
            operations=planned_ops,
            confidence=1.0,
            created_at=now,
        )
        return {**res, "plan": plan, "instruction_id": instruction_id}

    # Multi-part instructions
    sub_results = [_interpret_single(c, p) for p in parts]
    bad_idx = next((i for i, r in enumerate(sub_results) if r["kind"] != "ops"), None)
    if bad_idx is not None:
        bad_res = sub_results[bad_idx]
        bad_kind = bad_res["kind"]
        if bad_kind in ("undo", "redo", "restore_revision"):
            plan = OperationPlan(
                instruction_id=instruction_id,
                original_instruction=input_str,
                status="REJECTED",
                operations=[],
                reason="Undo, redo, and revision restore must be sent on their own.",
                confidence=0.0,
                created_at=now,
            )
            return {
                "kind": "reject",
                "code": "MIXED_INSTRUCTION",
                "message": "Undo, redo and revision restore must be sent on their own.",
                "details": {},
                "plan": plan,
            }
        if bad_kind == "clarify":
            msg = f"Part {bad_idx + 1} (“{parts[bad_idx]}”) needs a choice: {bad_res['question']}"
            plan = OperationPlan(
                instruction_id=instruction_id,
                original_instruction=input_str,
                status="NEEDS_CLARIFICATION",
                operations=[],
                clarification=ClarificationSchema(question=msg, options=[]),
                reason=msg,
                confidence=0.5,
                created_at=now,
            )
            return {
                **bad_res,
                "question": msg,
                "options": [],
                "notes": ["No part of this instruction was applied. Send that part on its own to choose an option."],
                "plan": plan,
            }
        msg = f"Part {bad_idx + 1} (“{parts[bad_idx]}”) was rejected: {bad_res['message']} No part was applied."
        plan = OperationPlan(
            instruction_id=instruction_id,
            original_instruction=input_str,
            status="REJECTED",
            operations=[],
            reason=msg,
            confidence=0.0,
            created_at=now,
        )
        return {
            **bad_res,
            "message": msg,
            "plan": plan,
        }

    combined_ops: List[Op] = []
    combined_notes: List[str] = []
    combined_planned: List[PlannedOperation] = []

    for idx, (p_text, r) in enumerate(zip(parts, sub_results)):
        for op in r["ops"]:
            combined_ops.append(op)
            op_idx = len(combined_ops)
            combined_planned.append(_op_to_planned(op, f"op_{op_idx}", notes=r.get("notes")))
        combined_notes.append(f"Part {idx + 1}: “{p_text}” → {', '.join(o['type'] for o in r['ops'])}")
        combined_notes.extend(r.get("notes", []))

    plan = OperationPlan(
        instruction_id=instruction_id,
        original_instruction=input_str,
        status="APPLIED",
        operations=combined_planned,
        confidence=1.0,
        created_at=now,
    )
    return {
        "kind": "ops",
        "ops": combined_ops,
        "notes": combined_notes,
        "planned_operations": combined_planned,
        "parts": parts,
        "plan": plan,
        "instruction_id": instruction_id,
    }
