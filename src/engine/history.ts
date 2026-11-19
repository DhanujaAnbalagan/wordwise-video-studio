import { timelineHash } from "./timeline";
import type { EngineError, History, Op, Revision, TimelineState } from "./types";
import { err } from "./operations";

export function createHistory(state: TimelineState, now = 0): History {
  const r0: Revision = {
    id: "r0",
    index: 0,
    parentId: null,
    kind: "initial",
    label: "Original upload",
    ops: [],
    state,
    hash: timelineHash(state),
    createdAt: now,
  };
  return { revisions: [r0], undo: ["r0"], redo: [] };
}

export function getRevision(h: History, id: string): Revision | undefined {
  return h.revisions.find((r) => r.id === id);
}

export function head(h: History): Revision {
  return getRevision(h, h.undo[h.undo.length - 1])!;
}

/** Every accepted edit creates exactly one revision and clears the redo branch. */
export function commit(
  h: History,
  entry: { kind: "edit" | "restore"; label: string; ops: Op[]; state: TimelineState },
  now = 0,
): { history: History; revision: Revision } {
  const index = h.revisions.length;
  const revision: Revision = {
    id: `r${index}`,
    index,
    parentId: head(h).id,
    kind: entry.kind,
    label: entry.label,
    ops: entry.ops,
    state: entry.state,
    hash: timelineHash(entry.state),
    createdAt: now,
  };
  return {
    history: { revisions: [...h.revisions, revision], undo: [...h.undo, revision.id], redo: [] },
    revision,
  };
}

export function undo(h: History): { history: History; undone: Revision } | { error: EngineError } {
  if (h.undo.length <= 1) return { error: err("NOTHING_TO_UNDO", "There is no earlier edit to undo.") };
  const undone = head(h);
  return { history: { ...h, undo: h.undo.slice(0, -1), redo: [...h.redo, undone.id] }, undone };
}

export function redo(h: History): { history: History; redone: Revision } | { error: EngineError } {
  if (!h.redo.length) return { error: err("NOTHING_TO_REDO", "There is no undone edit to redo.") };
  const id = h.redo[h.redo.length - 1];
  return { history: { ...h, undo: [...h.undo, id], redo: h.redo.slice(0, -1) }, redone: getRevision(h, id)! };
}
