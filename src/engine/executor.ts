import { commit, createHistory, getRevision, head, redo, undo } from "./history";
import { interpret, type InterpretContext } from "./interpreter";
import { applyOps, err } from "./operations";
import { initialState, summarize } from "./timeline";
import type { History, InstructionResult, Op, Transcript } from "./types";

export interface Session {
  transcript: Transcript;
  history: History;
  log: InstructionResult[];
}

export interface UiContext {
  selection?: string[];
  selectedCaptionId?: string | null;
  playheadSrcMs?: number;
}

export function createSession(transcript: Transcript, sourceDurationMs: number, now = 0): Session {
  return { transcript, history: createHistory(initialState(sourceDurationMs), now), log: [] };
}

function describeOps(ops: Op[]): string {
  return ops
    .map((o) => {
      switch (o.type) {
        case "cut":
          return o.reason === "filler_words" ? "remove fillers" : o.reason === "silence" ? "remove silence" : `cut ${o.ranges.length} range(s)`;
        case "restore":
          return "restore content";
        case "speed":
          return `speed ${o.speed}x`;
        case "add_caption":
          return `caption “${o.text}”`;
        case "style_caption":
          return "style caption";
        case "remove_caption":
          return "remove caption";
        case "move":
          return "reorder section";
        default:
          return o.type.replace("_", " ");
      }
    })
    .join(" + ");
}

function push(s: Session, history: History, r: Omit<InstructionResult, "id">): Session {
  return { ...s, history, log: [...s.log, { ...r, id: `ins_${s.log.length + 1}` }] };
}

/** Validates and applies ops atomically, creating exactly one revision. */
function applyAndCommit(s: Session, input: string, ops: Op[], notes: string[], started: number, now: number): Session {
  const words = new Map(s.transcript.words.map((w) => [w.id, w]));
  const cur = head(s.history);
  const before = summarize(cur.state, s.transcript.words);
  const base = { input, createdAt: now, notes, ops };
  const res = applyOps(cur.state, ops, words);
  if (!res.ok) {
    const prefix = ops.length > 1 ? `Operation ${res.failedIndex + 1} of ${ops.length} (${ops[res.failedIndex].type}) failed: ` : "";
    return push(s, s.history, {
      ...base,
      status: "rejected",
      latencyMs: performance.now() - started,
      message: `${prefix}${res.error.message}${ops.length > 1 ? " Nothing was applied." : ""}`,
      error: res.error,
      before,
    });
  }
  const after = summarize(res.state, s.transcript.words);
  if (after.hash === before.hash) {
    const e = err("NO_CHANGE", "The timeline is already in that state; nothing was changed.");
    return push(s, s.history, { ...base, status: "rejected", latencyMs: performance.now() - started, message: e.message, error: e, before });
  }
  const { history, revision } = commit(s.history, { kind: "edit", label: input, ops, state: res.state }, now);
  return push(s, history, {
    ...base,
    notes: [...notes, ...res.notes],
    status: "applied",
    latencyMs: performance.now() - started,
    message: `Applied ${describeOps(ops)} as revision ${revision.id}.`,
    revisionId: revision.id,
    before,
    after,
  });
}

export function runInstruction(s: Session, input: string, ui: UiContext = {}, now = Date.now()): Session {
  const started = performance.now();
  const cur = head(s.history);
  const ic: InterpretContext = {
    transcript: s.transcript,
    state: cur.state,
    selection: ui.selection ?? [],
    selectedCaptionId: ui.selectedCaptionId ?? null,
    playheadSrcMs: ui.playheadSrcMs,
  };
  const it = interpret(input, ic);
  const base = { input, createdAt: now, notes: [] as string[] };
  const lat = () => performance.now() - started;
  const before = summarize(cur.state, s.transcript.words);

  switch (it.kind) {
    case "reject": {
      const error = err(it.code, it.message, it.details ?? {});
      return push(s, s.history, { ...base, status: "rejected", latencyMs: lat(), message: it.message, error, before });
    }
    case "clarify":
      return push(s, s.history, {
        ...base,
        notes: it.notes,
        status: "needs_clarification",
        latencyMs: lat(),
        message: it.question,
        question: it.question,
        options: it.options,
        basedOnHash: before.hash,
        before,
      });
    case "undo": {
      const r = undo(s.history);
      if ("error" in r) return push(s, s.history, { ...base, status: "rejected", latencyMs: lat(), message: r.error.message, error: r.error, before });
      const h = head(r.history);
      return push(s, r.history, {
        ...base,
        status: "applied",
        latencyMs: lat(),
        ops: [],
        message: `Undid ${r.undone.id} (“${r.undone.label}”). Current revision is ${h.id}.`,
        revisionId: h.id,
        before,
        after: summarize(h.state, s.transcript.words),
      });
    }
    case "redo": {
      const r = redo(s.history);
      if ("error" in r) return push(s, s.history, { ...base, status: "rejected", latencyMs: lat(), message: r.error.message, error: r.error, before });
      return push(s, r.history, {
        ...base,
        status: "applied",
        latencyMs: lat(),
        ops: r.redone.ops,
        message: `Redid ${r.redone.id} (“${r.redone.label}”).`,
        revisionId: r.redone.id,
        before,
        after: summarize(r.redone.state, s.transcript.words),
      });
    }
    case "restore_revision": {
      const target = getRevision(s.history, it.revisionId);
      if (!target) {
        const e = err("UNKNOWN_REVISION", `Revision ${it.revisionId} does not exist.`, { revisionId: it.revisionId });
        return push(s, s.history, { ...base, status: "rejected", latencyMs: lat(), message: e.message, error: e, before });
      }
      if (target.hash === before.hash) {
        const e = err("NO_CHANGE", `The timeline already matches ${target.id}.`);
        return push(s, s.history, { ...base, status: "rejected", latencyMs: lat(), message: e.message, error: e, before });
      }
      const { history, revision } = commit(s.history, { kind: "restore", label: `Restore ${target.id}`, ops: [], state: target.state }, now);
      return push(s, history, {
        ...base,
        status: "applied",
        latencyMs: lat(),
        ops: [],
        notes: ["The original source video is untouched; captions and anchors are recalculated from the restored state."],
        message: `Restored ${target.id} as new revision ${revision.id}. Undo returns to the previous state.`,
        revisionId: revision.id,
        before,
        after: summarize(revision.state, s.transcript.words),
      });
    }
    case "ops":
      return applyAndCommit(s, input, it.ops, it.notes, started, now);
  }
}

/** Applies one clarification option exactly once, only if the timeline hasn't changed since. */
export function chooseOption(s: Session, resultId: string, optionIndex: number, now = Date.now()): Session {
  const started = performance.now();
  const r = s.log.find((x) => x.id === resultId);
  const fail = (code: string, message: string) =>
    push(s, s.history, { input: `(option ${optionIndex + 1} for ${resultId})`, createdAt: now, notes: [], status: "rejected", latencyMs: 0, message, error: err(code, message) });
  if (!r || r.status !== "needs_clarification" || !r.options?.[optionIndex]) return fail("UNKNOWN_OPTION", "That option no longer exists.");
  if (r.resolvedWith != null) return fail("ALREADY_RESOLVED", "That question was already answered; the edit was not applied twice.");
  if (head(s.history).hash !== r.basedOnHash) return fail("STALE_CLARIFICATION", "The timeline changed since this question was asked. Please send the instruction again.");
  const opt = r.options[optionIndex];
  const marked: Session = { ...s, log: s.log.map((x) => (x.id === resultId ? { ...x, resolvedWith: optionIndex } : x)) };
  return applyAndCommit(marked, `${r.input} → ${opt.label}`, opt.ops, [`Chosen option: ${opt.label}${opt.detail ? ` (${opt.detail})` : ""}.`], started, now);
}

export function currentState(s: Session) {
  return head(s.history).state;
}
