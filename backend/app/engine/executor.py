"""Instruction executor: the only place where timeline state changes.

Flow (mirrors ``src/engine/executor.ts``)::

    text --interpreter--> Interpretation (ops | clarify | reject | undo | redo | restore)
         --executor-----> validate + apply ops atomically --> exactly one revision

* The interpreter never mutates state; it only proposes structured ops.
* ``apply_ops`` is all-or-nothing: a failed op leaves the timeline untouched
  and creates no revision.
* An edit that does not change the timeline hash is rejected (``NO_CHANGE``).
* Clarification options are stored in the log and can be applied exactly once,
  and only if the timeline has not changed since the question was asked.

Every result has ``status`` in {"applied", "needs_clarification", "rejected"}.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import history as hist
from .operations import apply_ops
from .serialize import initial_state, op_from_dict, ops_to_list
from .timeline import layout, place_word, timeline_duration
from .types import History, TimelineState, Transcript, Word

ENGINE_VERSION = "videoflow-engine-py 1.0.0"


@dataclass
class Session:
    transcript: Transcript
    history: History
    log: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class UiContext:
    selection: List[str] = field(default_factory=list)
    selected_caption_id: Optional[str] = None
    playhead_src_ms: Optional[int] = None


def create_session(transcript: Transcript, source_duration_ms: int, now: int = 0) -> Session:
    return Session(transcript=transcript, history=hist.create_history(initial_state(source_duration_ms), now), log=[])


def summarize(state: TimelineState, words: List[Word]) -> Dict[str, Any]:
    """Compact, comparable description of a state (shown before/after each edit)."""
    from .serialize import timeline_hash

    placed = layout(state.segments)
    vis = {w.id for w in words if place_word(placed, w)["visible"]}
    cap_vis = sum(1 for c in state.captions if any(wid in vis for wid in c.wordIds))
    return {
        "durationTl": round(timeline_duration(state.segments)),
        "segments": len(state.segments),
        "visibleWords": len(vis),
        "captionsVisible": cap_vis,
        "captionsHidden": len(state.captions) - cap_vis,
        "effects": len(state.effects),
        "overlays": len(state.overlays),
        "hash": timeline_hash(state),
    }


def _describe(ops: List[Dict[str, Any]]) -> str:
    parts = []
    for o in ops:
        t = o["type"]
        if t == "cut":
            r = o.get("reason")
            parts.append("remove fillers" if r == "filler_words" else "remove silence" if r == "silence" else f"cut {len(o['ranges'])} range(s)")
        elif t == "keep":
            parts.append(f"keep {len(o['ranges'])} range(s)")
        elif t == "restore":
            parts.append("restore content")
        elif t == "speed":
            parts.append(f"speed {o['speed']:g}x")
        elif t == "add_caption":
            parts.append(f"caption \u201c{o['text']}\u201d")
        elif t == "add_text_overlay":
            parts.append(f"text overlay \u201c{o['text']}\u201d")
        elif t == "move":
            parts.append("reorder section")
        else:
            parts.append(t.replace("_", " "))
    return " + ".join(parts)


def _err(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": code, "message": message, "retryable": False, "details": details or {}}


def _push(s: Session, history: History, result: Dict[str, Any]) -> Session:
    entry = {**result, "id": f"ins_{len(s.log) + 1}"}
    return Session(transcript=s.transcript, history=history, log=[*s.log, entry])


def _lat(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _apply_and_commit(s: Session, text: str, ops: List[Dict[str, Any]], notes: List[str], started: float, now: int) -> Session:
    words = {w.id: w for w in s.transcript.words}
    cur = hist.head(s.history)
    before = summarize(cur.state, s.transcript.words)
    base = {"input": text, "createdAt": now, "notes": list(notes), "ops": ops_to_list(ops)}
    res = apply_ops(hist.head_state(s.history), ops, words)
    if not res["ok"]:
        e = res["error"]
        i = res["failedIndex"]
        prefix = f"Operation {i + 1} of {len(ops)} ({ops[i]['type']}) failed: " if len(ops) > 1 else ""
        suffix = " Nothing was applied." if len(ops) > 1 else ""
        error = _err(e.code, e.message, {k: str(v) for k, v in (e.details or {}).items()})
        return _push(s, s.history, {**base, "status": "rejected", "latencyMs": _lat(started), "message": f"{prefix}{e.message}{suffix}", "error": error, "before": before})
    after = summarize(res["state"], s.transcript.words)
    if after["hash"] == before["hash"]:
        error = _err("NO_CHANGE", "The timeline is already in that state; nothing was changed.")
        return _push(s, s.history, {**base, "status": "rejected", "latencyMs": _lat(started), "message": error["message"], "error": error, "before": before})
    history, rev = hist.commit(s.history, kind="edit", label=text, ops=ops, state=res["state"], now=now)
    return _push(s, history, {
        **base, "notes": [*notes, *res["notes"]], "status": "applied", "latencyMs": _lat(started),
        "message": f"Applied {_describe(ops)} as revision {rev.id}.", "revisionId": rev.id, "before": before, "after": after,
    })


def run_instruction(s: Session, text: str, ui: Optional[UiContext] = None, now: Optional[int] = None, silences: Optional[list] = None) -> Session:
    """Interpret ``text`` and apply it. Returns a new Session; ``log[-1]`` is the result."""
    from .interpreter import InterpretContext, interpret

    ui = ui or UiContext()
    now = int(time.time() * 1000) if now is None else now
    started = time.perf_counter()
    cur = hist.head(s.history)
    before = summarize(cur.state, s.transcript.words)
    base = {"input": text, "createdAt": now, "notes": []}
    if not text or not text.strip():
        e = _err("EMPTY_INSTRUCTION", "Type an editing instruction first.")
        return _push(s, s.history, {**base, "status": "rejected", "latencyMs": 0, "message": e["message"], "error": e, "before": before})
    ctx = InterpretContext(
        transcript=s.transcript, state=hist.head_state(s.history), selection=list(ui.selection),
        selected_caption_id=ui.selected_caption_id, playhead_src_ms=ui.playhead_src_ms, silences=silences,
    )
    it = interpret(text, ctx)
    kind = it["kind"]

    if kind == "reject":
        e = _err(it["code"], it["message"], it.get("details") or {})
        return _push(s, s.history, {**base, "status": "rejected", "latencyMs": _lat(started), "message": it["message"], "error": e, "before": before})

    if kind == "clarify":
        options = [{"label": o["label"], "detail": o.get("detail"), "ops": ops_to_list(o["ops"])} for o in it["options"]]
        return _push(s, s.history, {
            **base, "notes": list(it.get("notes") or []), "status": "needs_clarification", "latencyMs": _lat(started),
            "message": it["question"], "question": it["question"], "options": options, "basedOnHash": before["hash"], "before": before,
        })

    if kind == "undo":
        r = hist.undo(s.history)
        if not isinstance(r, hist.UndoResult):
            e = _err(r.code, r.message)
            return _push(s, s.history, {**base, "status": "rejected", "latencyMs": _lat(started), "message": r.message, "error": e, "before": before})
        h = hist.head(r.history)
        return _push(s, r.history, {
            **base, "status": "applied", "latencyMs": _lat(started), "ops": [],
            "message": f"Undid {r.undone.id} (\u201c{r.undone.label}\u201d). Current revision is {h.id}.",
            "revisionId": h.id, "before": before, "after": summarize(h.state, s.transcript.words),
        })

    if kind == "redo":
        r = hist.redo(s.history)
        if not isinstance(r, hist.RedoResult):
            e = _err(r.code, r.message)
            return _push(s, s.history, {**base, "status": "rejected", "latencyMs": _lat(started), "message": r.message, "error": e, "before": before})
        return _push(s, r.history, {
            **base, "status": "applied", "latencyMs": _lat(started), "ops": list(r.redone.ops),
            "message": f"Redid {r.redone.id} (\u201c{r.redone.label}\u201d).", "revisionId": r.redone.id,
            "before": before, "after": summarize(r.redone.state, s.transcript.words),
        })

    if kind == "restore_revision":
        return restore_revision(s, it["revisionId"], text=text, now=now)

    return _apply_and_commit(s, text, list(it["ops"]), list(it.get("notes") or []), started, now)


def restore_revision(s: Session, revision_id: str, text: Optional[str] = None, now: Optional[int] = None) -> Session:
    """Make an older revision current by committing a new ``restore`` revision."""
    now = int(time.time() * 1000) if now is None else now
    started = time.perf_counter()
    text = text or f"Restore {revision_id}"
    before = summarize(hist.head(s.history).state, s.transcript.words)
    base = {"input": text, "createdAt": now, "notes": []}
    target = hist.get_revision(s.history, revision_id)
    if target is None:
        e = _err("UNKNOWN_REVISION", f"Revision {revision_id} does not exist.", {"revisionId": revision_id})
        return _push(s, s.history, {**base, "status": "rejected", "latencyMs": _lat(started), "message": e["message"], "error": e, "before": before})
    if target.hash == before["hash"]:
        e = _err("NO_CHANGE", f"The timeline already matches {target.id}.")
        return _push(s, s.history, {**base, "status": "rejected", "latencyMs": _lat(started), "message": e["message"], "error": e, "before": before})
    history, rev = hist.commit(s.history, kind="restore", label=f"Restore {target.id}", ops=[], state=target.state, now=now)
    return _push(s, history, {
        **base, "status": "applied", "latencyMs": _lat(started), "ops": [],
        "notes": ["The original source video is untouched; captions and anchors are recalculated from the restored state."],
        "message": f"Restored {target.id} as new revision {rev.id}. Undo returns to the previous state.",
        "revisionId": rev.id, "before": before, "after": summarize(rev.state, s.transcript.words),
    })


def undo(s: Session, now: Optional[int] = None) -> Session:
    return _direct(s, "undo", now)


def redo(s: Session, now: Optional[int] = None) -> Session:
    return _direct(s, "redo", now)


def _direct(s: Session, which: str, now: Optional[int]) -> Session:
    """Undo/redo without going through text interpretation (API buttons)."""
    now = int(time.time() * 1000) if now is None else now
    started = time.perf_counter()
    before = summarize(hist.head(s.history).state, s.transcript.words)
    base = {"input": which, "createdAt": now, "notes": []}
    r = hist.undo(s.history) if which == "undo" else hist.redo(s.history)
    if isinstance(r, hist.UndoResult):
        h = hist.head(r.history)
        return _push(s, r.history, {**base, "status": "applied", "latencyMs": _lat(started), "ops": [],
                                    "message": f"Undid {r.undone.id} (\u201c{r.undone.label}\u201d). Current revision is {h.id}.",
                                    "revisionId": h.id, "before": before, "after": summarize(h.state, s.transcript.words)})
    if isinstance(r, hist.RedoResult):
        return _push(s, r.history, {**base, "status": "applied", "latencyMs": _lat(started), "ops": list(r.redone.ops),
                                    "message": f"Redid {r.redone.id} (\u201c{r.redone.label}\u201d).", "revisionId": r.redone.id,
                                    "before": before, "after": summarize(r.redone.state, s.transcript.words)})
    e = _err(r.code, r.message)
    return _push(s, s.history, {**base, "status": "rejected", "latencyMs": _lat(started), "message": r.message, "error": e, "before": before})


def choose_option(s: Session, result_id: str, option_index: int, now: Optional[int] = None) -> Session:
    """Apply one clarification option exactly once, only if the timeline hasn't changed since."""
    now = int(time.time() * 1000) if now is None else now
    started = time.perf_counter()

    def fail(code: str, message: str) -> Session:
        e = _err(code, message)
        return _push(s, s.history, {"input": f"(option {option_index + 1} for {result_id})", "createdAt": now, "notes": [],
                                    "status": "rejected", "latencyMs": 0, "message": message, "error": e})

    r = next((x for x in s.log if x["id"] == result_id), None)
    opts = (r or {}).get("options") or []
    if not r or r.get("status") != "needs_clarification" or not (0 <= option_index < len(opts)):
        return fail("UNKNOWN_OPTION", "That option no longer exists.")
    if r.get("resolvedWith") is not None:
        return fail("ALREADY_RESOLVED", "That question was already answered; the edit was not applied twice.")
    if hist.head(s.history).hash != r.get("basedOnHash"):
        return fail("STALE_CLARIFICATION", "The timeline changed since this question was asked. Please send the instruction again.")
    opt = opts[option_index]
    marked = Session(transcript=s.transcript, history=s.history,
                     log=[{**x, "resolvedWith": option_index} if x["id"] == result_id else x for x in s.log])
    detail = f" ({opt['detail']})" if opt.get("detail") else ""
    return _apply_and_commit(marked, f"{r['input']} \u2192 {opt['label']}", [op_from_dict(o) for o in opt["ops"]],
                             [f"Chosen option: {opt['label']}{detail}."], started, now)


def current_state(s: Session) -> TimelineState:
    return hist.head_state(s.history)
