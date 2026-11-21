"""Revision history: undo, redo and restore for a project's timeline.

Mirrors ``src/engine/history.ts``. A history is an append-only list of
revisions plus two stacks:

* ``undo`` – the applied path; its last element is the current head.
* ``redo`` – revisions that were undone and can be re-applied.

Rules
-----
* Every accepted edit creates exactly one revision (``commit``) and clears
  the redo branch.
* Revision IDs are ``r<index>``: stable, unique within a project, never reused.
* Every state stored in a revision is a deep, independent copy, so later edits
  can never mutate history through shared nested objects.
* All functions are pure: they return a new ``History`` and never mutate inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional, Union

from .serialize import clone_state, ops_to_list, state_from_dict, state_to_dict, timeline_hash
from .types import EngineError, History, Revision, TimelineState


def _err(code: str, message: str) -> EngineError:
    return EngineError(code=code, message=message, retryable=False, details={})


def create_history(state: TimelineState, now: int = 0) -> History:
    snap = clone_state(state)
    r0 = Revision(
        id="r0", index=0, parentId=None, kind="initial", label="Original upload",
        ops=[], state=snap, hash=timeline_hash(snap), createdAt=now,
    )
    return History(revisions=[r0], undo=["r0"], redo=[])


def get_revision(h: History, revision_id: str) -> Optional[Revision]:
    return next((r for r in h.revisions if r.id == revision_id), None)


def head(h: History) -> Revision:
    rev = get_revision(h, h.undo[-1])
    if rev is None:  # pragma: no cover - guarded by construction
        raise RuntimeError("History head points at a missing revision.")
    return rev


def head_state(h: History) -> TimelineState:
    """An independent copy of the current state (safe to hand to callers)."""
    return clone_state(head(h).state)


def commit(h: History, *, kind: str, label: str, ops: list, state: TimelineState, now: int = 0) -> tuple[History, Revision]:
    """Append one revision on top of the head and clear the redo branch."""
    if kind not in ("edit", "restore"):
        raise ValueError("kind must be 'edit' or 'restore'")
    index = len(h.revisions)
    snap = clone_state(state)
    rev = Revision(
        id=f"r{index}", index=index, parentId=head(h).id, kind=kind, label=label,
        ops=ops_to_list(ops), state=snap, hash=timeline_hash(snap), createdAt=now,
    )
    return History(revisions=[*h.revisions, rev], undo=[*h.undo, rev.id], redo=[]), rev


@dataclass(frozen=True)
class UndoResult:
    history: History
    undone: Revision


@dataclass(frozen=True)
class RedoResult:
    history: History
    redone: Revision


def undo(h: History) -> Union[UndoResult, EngineError]:
    if len(h.undo) <= 1:
        return _err("NOTHING_TO_UNDO", "There is no earlier edit to undo.")
    undone = head(h)
    return UndoResult(History(revisions=list(h.revisions), undo=h.undo[:-1], redo=[*h.redo, undone.id]), undone)


def redo(h: History) -> Union[RedoResult, EngineError]:
    if not h.redo:
        return _err("NOTHING_TO_REDO", "There is no undone edit to redo.")
    rid = h.redo[-1]
    rev = get_revision(h, rid)
    assert rev is not None
    return RedoResult(History(revisions=list(h.revisions), undo=[*h.undo, rid], redo=h.redo[:-1]), rev)


# ---------------------------------------------------------------------------
# JSON form (used for persistence)
# ---------------------------------------------------------------------------


def revision_to_dict(r: Revision) -> dict:
    return {
        "id": r.id, "index": r.index, "parentId": r.parentId, "kind": r.kind, "label": r.label,
        "ops": ops_to_list(r.ops), "state": state_to_dict(r.state), "hash": r.hash, "createdAt": r.createdAt,
    }


def revision_from_dict(d: Mapping) -> Revision:
    return Revision(
        id=d["id"], index=int(d["index"]), parentId=d.get("parentId"), kind=d["kind"], label=d["label"],
        ops=list(d.get("ops") or []), state=state_from_dict(d["state"]), hash=d["hash"], createdAt=int(d.get("createdAt") or 0),
    )


def history_to_dict(h: History) -> dict:
    return {"revisions": [revision_to_dict(r) for r in h.revisions], "undo": list(h.undo), "redo": list(h.redo)}


def history_from_dict(d: Mapping) -> History:
    return History(revisions=[revision_from_dict(r) for r in d["revisions"]], undo=list(d["undo"]), redo=list(d["redo"]))
