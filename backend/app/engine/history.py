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

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple, Union

from .operations import apply_ops
from .serialize import (
    clone_state,
    ops_to_list,
    state_from_dict,
    state_to_dict,
    timeline_hash,
)
from .types import (
    AudioEffect,
    Caption,
    EngineError,
    History,
    Op,
    Revision,
    Segment,
    TextOverlay,
    TimelineState,
    Word,
)


def _err(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> EngineError:
    return EngineError(code=code, message=message, retryable=False, details=details or {})


def clone_timeline_state(state: TimelineState) -> TimelineState:
    """Safely deep-clone a TimelineState so that subsequent mutations to the original
    or nested collections do not affect this snapshot in any way.
    """
    return clone_state(state)


def create_history(
    state: TimelineState,
    now: int = 0,
    label: str = "Original upload",
    source_video_path: Optional[str] = None,
    source_hash: Optional[str] = None,
) -> History:
    """Create a new History container initialized with revision 'r0'."""
    snap = clone_state(state)
    r0 = Revision(
        id="r0",
        index=0,
        parentId=None,
        kind="initial",
        label=label,
        ops=[],
        state=snap,
        hash=timeline_hash(snap),
        createdAt=now,
    )
    return History(
        revisions=[r0],
        undo=["r0"],
        redo=[],
        sourceVideoPath=source_video_path,
        sourceHash=source_hash,
    )


def get_revision(h: History, revision_id: str) -> Optional[Revision]:
    """Retrieve a revision by its ID."""
    return next((r for r in h.revisions if r.id == revision_id), None)


def head(h: History) -> Revision:
    """Return the current head revision (the active state)."""
    if not h.undo:
        raise ValueError("History undo stack is empty; invalid history state.")
    head_id = h.undo[-1]
    rev = get_revision(h, head_id)
    if rev is None:
        raise RuntimeError(f"History head points at a missing revision '{head_id}'.")
    return rev


def head_state(h: History) -> TimelineState:
    """An independent copy of the current state (safe to hand to callers)."""
    return clone_state(head(h).state)


current_state = head_state


def commit(
    h: History,
    state: Optional[TimelineState] = None,
    ops: Optional[List[Any]] = None,
    label: str = "",
    kind: str = "edit",
    now: int = 0,
    **kwargs: Any,
) -> Tuple[History, Revision]:
    """Append one revision on top of the head and clear the redo branch."""
    if "state" in kwargs:
        state = kwargs["state"]
    if "ops" in kwargs:
        ops = kwargs["ops"]
    if "label" in kwargs:
        label = kwargs["label"]
    if "kind" in kwargs:
        kind = kwargs["kind"]
    if "now" in kwargs:
        now = kwargs["now"]

    if state is None:
        raise ValueError("state is required for commit")
    if kind not in ("edit", "restore"):
        raise ValueError("kind must be 'edit' or 'restore'")

    ops_list = ops_to_list(ops) if ops is not None else []
    index = len(h.revisions)
    snap = clone_state(state)
    rev = Revision(
        id=f"r{index}",
        index=index,
        parentId=head(h).id,
        kind=kind,
        label=label,
        ops=ops_list,
        state=snap,
        hash=timeline_hash(snap),
        createdAt=now,
    )
    new_history = History(
        revisions=[*h.revisions, rev],
        undo=[*h.undo, rev.id],
        redo=[],  # applying a new edit after undo clears redo history
        sourceVideoPath=h.sourceVideoPath,
        sourceHash=h.sourceHash,
    )
    return new_history, rev


@dataclass(frozen=True)
class UndoResult:
    history: History
    undone: Revision

    def __iter__(self):
        yield self.history
        yield self.undone


@dataclass(frozen=True)
class RedoResult:
    history: History
    redone: Revision

    def __iter__(self):
        yield self.history
        yield self.redone


def can_undo(h: History) -> bool:
    """Return True if an earlier revision exists to undo to."""
    return len(h.undo) > 1


def undo(h: History, raise_on_error: bool = False) -> Union[UndoResult, EngineError]:
    """Undo the current revision, restoring the previous head."""
    if len(h.undo) <= 1:
        err = _err("NOTHING_TO_UNDO", "There is no earlier edit to undo.")
        if raise_on_error:
            raise ValueError(f"{err.code}: {err.message}")
        return err
    undone = head(h)
    new_history = History(
        revisions=list(h.revisions),
        undo=h.undo[:-1],
        redo=[*h.redo, undone.id],
        sourceVideoPath=h.sourceVideoPath,
        sourceHash=h.sourceHash,
    )
    return UndoResult(new_history, undone)


def can_redo(h: History) -> bool:
    """Return True if an undone revision exists to redo."""
    return len(h.redo) > 0


def redo(h: History, raise_on_error: bool = False) -> Union[RedoResult, EngineError]:
    """Redo the most recently undone revision."""
    if not h.redo:
        err = _err("NOTHING_TO_REDO", "There is no undone edit to redo.")
        if raise_on_error:
            raise ValueError(f"{err.code}: {err.message}")
        return err
    rid = h.redo[-1]
    rev = get_revision(h, rid)
    assert rev is not None
    new_history = History(
        revisions=list(h.revisions),
        undo=[*h.undo, rid],
        redo=h.redo[:-1],
        sourceVideoPath=h.sourceVideoPath,
        sourceHash=h.sourceHash,
    )
    return RedoResult(new_history, rev)


def restore_revision(
    h: History,
    revision_id: str,
    now: int = 0,
) -> Tuple[History, Revision]:
    """Restore the timeline state from an earlier revision as a new revision.

    Preserves the complete earlier state. Undo returns to the state prior to restore.
    Raises ValueError if the target revision does not exist or matches current head.
    """
    target = get_revision(h, revision_id)
    if target is None:
        raise ValueError(f"UNKNOWN_REVISION: Revision '{revision_id}' does not exist.")

    cur_head = head(h)
    if target.hash == cur_head.hash:
        raise ValueError(
            f"NO_CHANGE: The timeline already matches revision '{revision_id}'."
        )

    return commit(
        h,
        state=target.state,
        ops=[],
        label=f"Restore {target.id}",
        kind="restore",
        now=now,
    )


def apply_and_commit(
    h: History,
    ops: List[Op],
    words: Dict[str, Word],
    label: str,
    now: int = 0,
) -> Dict[str, Any]:
    """Atomically apply a list of operations to the current head state and commit."""
    cur = head(h)
    res = apply_ops(cur.state, ops, words)
    if not res["ok"]:
        return {
            "ok": False,
            "error": res["error"],
            "failedIndex": res.get("failedIndex", 0),
            "history": h,
        }

    new_state = res["state"]
    new_hash = timeline_hash(new_state)
    if new_hash == cur.hash:
        return {
            "ok": False,
            "error": _err(
                "NO_CHANGE",
                "The timeline is already in that state; nothing was changed.",
            ),
            "history": h,
        }

    new_h, rev = commit(h, state=new_state, ops=ops, label=label, kind="edit", now=now)
    return {
        "ok": True,
        "history": new_h,
        "revision": rev,
        "notes": res["notes"],
    }


# ---------------------------------------------------------------------------
# JSON form (used for persistence)
# ---------------------------------------------------------------------------


def revision_to_dict(r: Revision) -> dict:
    return {
        "id": r.id,
        "index": r.index,
        "parentId": r.parentId,
        "kind": r.kind,
        "label": r.label,
        "ops": ops_to_list(r.ops),
        "state": state_to_dict(r.state),
        "hash": r.hash,
        "createdAt": r.createdAt,
    }


def revision_from_dict(d: Mapping) -> Revision:
    return Revision(
        id=d["id"],
        index=int(d["index"]),
        parentId=d.get("parentId"),
        kind=d["kind"],
        label=d["label"],
        ops=list(d.get("ops") or []),
        state=state_from_dict(d["state"]),
        hash=d["hash"],
        createdAt=int(d.get("createdAt") or 0),
    )


def history_to_dict(h: History) -> dict:
    return {
        "revisions": [revision_to_dict(r) for r in h.revisions],
        "undo": list(h.undo),
        "redo": list(h.redo),
    }


def history_from_dict(d: Mapping) -> History:
    return History(
        revisions=[revision_from_dict(r) for r in d["revisions"]],
        undo=list(d["undo"]),
        redo=list(d["redo"]),
    )


class RevisionManager:
    """Stateful revision manager for VideoFlow AI.

    Manages revision tracking, original media references, undo/redo stacks,
    and ensures complete snapshot isolation across all edits.
    """

    def __init__(
        self,
        initial_state: TimelineState,
        source_video_path: Optional[str] = None,
        source_hash: Optional[str] = None,
        now: int = 0,
        label: str = "Original upload",
    ) -> None:
        self._history = create_history(
            initial_state,
            now=now,
            label=label,
            source_video_path=source_video_path,
            source_hash=source_hash,
        )

    @property
    def source_video_path(self) -> Optional[str]:
        return self._history.sourceVideoPath

    @property
    def source_hash(self) -> Optional[str]:
        return self._history.sourceHash

    @property
    def history(self) -> History:
        return self._history

    @property
    def revisions(self) -> List[Revision]:
        return list(self._history.revisions)

    @property
    def current_revision(self) -> Revision:
        return head(self._history)

    @property
    def current_state(self) -> TimelineState:
        return current_state(self._history)

    @property
    def can_undo(self) -> bool:
        return can_undo(self._history)

    @property
    def can_redo(self) -> bool:
        return can_redo(self._history)

    def get_revision(self, revision_id: str) -> Optional[Revision]:
        return get_revision(self._history, revision_id)

    def apply(
        self,
        ops: List[Op],
        words: Dict[str, Word],
        label: str,
        now: int = 0,
    ) -> Dict[str, Any]:
        """Apply editing operations atomically. Creates a revision only on success."""
        res = apply_and_commit(self._history, ops, words, label=label, now=now)
        if res["ok"]:
            self._history = res["history"]
        return res

    def undo(self) -> Revision:
        """Undo the current edit, moving back one revision."""
        res = undo(self._history, raise_on_error=True)
        assert isinstance(res, UndoResult)
        self._history = res.history
        return res.undone

    def redo(self) -> Revision:
        """Redo the most recently undone edit."""
        res = redo(self._history, raise_on_error=True)
        assert isinstance(res, RedoResult)
        self._history = res.history
        return res.redone

    def restore(self, revision_id: str, now: int = 0) -> Revision:
        """Restore an earlier revision state as a new revision."""
        self._history, new_rev = restore_revision(self._history, revision_id, now=now)
        return new_rev
