import {
  intervalMinus,
  layout,
  normalizeSegments,
  placeWord,
  presentIntervals,
  presentMs,
  splitAt,
  subtractRange,
  timelineDuration,
} from "./timeline";
import type { CaptionSize, EngineError, Interval, Op, TimelineState, Word } from "./types";

export const MIN_SPEED = 0.25;
export const MAX_SPEED = 4;
/** Anything shorter than this after an edit counts as cutting the whole video. */
export const MIN_TIMELINE_MS = 100;

export type OpResult =
  | { ok: true; state: TimelineState; notes: string[] }
  | { ok: false; error: EngineError };

export function err(code: string, message: string, details: Record<string, unknown> = {}): EngineError {
  return { code, message, retryable: false, details };
}

const SIZES: CaptionSize[] = ["sm", "md", "lg", "xl"];

function validateRange(r: Interval, state: TimelineState): EngineError | null {
  if (!Number.isInteger(r.start) || !Number.isInteger(r.end))
    return err("INVALID_RANGE", "Range bounds must be whole milliseconds.", { range: r });
  if (r.start < 0 || r.end > state.sourceDurationMs)
    return err("RANGE_OUT_OF_BOUNDS", "Range lies outside the source video.", {
      range: r,
      sourceDurationMs: state.sourceDurationMs,
    });
  if (r.end <= r.start) return err("INVALID_RANGE", "Range end must be after its start.", { range: r });
  return null;
}

function clone(state: TimelineState): TimelineState {
  return {
    ...state,
    segments: state.segments.map((s) => ({ ...s })),
    captions: state.captions.map((c) => ({ ...c, wordIds: [...c.wordIds] })),
    effects: state.effects.map((e) => ({ ...e })),
  };
}

/** Pure, deterministic application of one validated operation. Never mutates `input`. */
export function applyOp(input: TimelineState, op: Op, words: Map<string, Word>): OpResult {
  const state = clone(input);
  const notes: string[] = [];

  switch (op.type) {
    case "cut": {
      if (!op.ranges.length) return { ok: false, error: err("INVALID_OPERATION", "Cut has no ranges.") };
      for (const r of op.ranges) {
        const e = validateRange(r, state);
        if (e) return { ok: false, error: e };
      }
      const removable = op.ranges.reduce((a, r) => a + presentMs(state.segments, r), 0);
      if (removable < 1)
        return {
          ok: false,
          error: err("NO_CHANGE", "That content is already removed, so nothing was changed.", {
            ranges: op.ranges,
          }),
        };
      let segs = state.segments;
      for (const r of op.ranges) segs = subtractRange(segs, r);
      segs = normalizeSegments(segs);
      if (timelineDuration(segs) < MIN_TIMELINE_MS)
        return {
          ok: false,
          error: err("WHOLE_VIDEO_CUT", "This edit would remove the entire video, so it was not applied.", {
            ranges: op.ranges,
          }),
        };
      state.segments = segs;
      notes.push(`Removed ${(removable / 1000).toFixed(3)}s of source content.`);
      return { ok: true, state, notes };
    }

    case "restore": {
      for (const r of op.ranges) {
        const e = validateRange(r, state);
        if (e) return { ok: false, error: e };
      }
      const missing = op.ranges
        .flatMap((r) => intervalMinus(r, presentIntervals(state.segments)))
        .sort((a, b) => a.start - b.start);
      if (!missing.length)
        return {
          ok: false,
          error: err("NO_CHANGE", "Nothing in that range is cut, so nothing needs restoring.", {
            ranges: op.ranges,
          }),
        };
      let segs = [...state.segments];
      for (const piece of missing) {
        const seg = { srcStart: piece.start, srcEnd: piece.end, speed: 1 };
        // Insert next to its source neighbour so restored content returns to its original place.
        let idx = segs.findIndex((s) => s.srcEnd === piece.start);
        if (idx >= 0) {
          segs.splice(idx + 1, 0, seg);
          continue;
        }
        idx = segs.findIndex((s) => s.srcStart === piece.end);
        if (idx >= 0) {
          segs.splice(idx, 0, seg);
          continue;
        }
        let pred = -1;
        segs.forEach((s, i) => {
          if (s.srcEnd <= piece.start && (pred < 0 || s.srcEnd > segs[pred].srcEnd)) pred = i;
        });
        if (pred >= 0) segs.splice(pred + 1, 0, seg);
        else segs.unshift(seg);
      }
      segs = normalizeSegments(segs);
      state.segments = segs;
      const restored = missing.reduce((a, p) => a + p.end - p.start, 0);
      notes.push(`Restored ${(restored / 1000).toFixed(3)}s of source content at its original position (1x speed).`);
      return { ok: true, state, notes };
    }

    case "speed": {
      const e = validateRange(op.range, state);
      if (e) return { ok: false, error: e };
      if (!(op.speed >= MIN_SPEED && op.speed <= MAX_SPEED))
        return {
          ok: false,
          error: err("INVALID_SPEED", `Speed must be between ${MIN_SPEED}x and ${MAX_SPEED}x.`, { speed: op.speed }),
        };
      if (presentMs(state.segments, op.range) < 1)
        return { ok: false, error: err("NO_VISIBLE_CONTENT", "That range is entirely cut.", { range: op.range }) };
      let segs = splitAt(splitAt(state.segments, op.range.start), op.range.end);
      let changed = false;
      segs = segs.map((s) => {
        if (s.srcStart >= op.range.start && s.srcEnd <= op.range.end && s.speed !== op.speed) {
          changed = true;
          return { ...s, speed: op.speed };
        }
        return s;
      });
      if (!changed)
        return { ok: false, error: err("NO_CHANGE", `That range already plays at ${op.speed}x.`, { range: op.range }) };
      state.segments = normalizeSegments(segs);
      return { ok: true, state, notes };
    }

    case "mute":
    case "bleep":
    case "clear_audio": {
      const targets = op.wordIds.map((id) => words.get(id));
      if (!targets.length || targets.some((w) => !w))
        return { ok: false, error: err("UNKNOWN_WORD", "One or more word IDs do not exist.", { wordIds: op.wordIds }) };
      if (op.type === "clear_audio") {
        const before = state.effects.length;
        state.effects = state.effects.filter((fx) => !op.wordIds.includes(fx.wordId));
        if (state.effects.length === before)
          return { ok: false, error: err("NO_CHANGE", "Those words have no mute or bleep.") };
        return { ok: true, state, notes };
      }
      let added = 0;
      for (const w of targets as Word[]) {
        if (state.effects.some((fx) => fx.wordId === w.id && fx.kind === op.type)) continue;
        state.effects.push({ id: `fx_${state.nextId++}`, kind: op.type, wordId: w.id, srcStart: w.start, srcEnd: w.end });
        added++;
      }
      if (!added)
        return { ok: false, error: err("NO_CHANGE", `Those words are already ${op.type === "mute" ? "muted" : "bleeped"}.`) };
      notes.push(`${op.type === "mute" ? "Muted" : "Bleeped"} ${added} word(s).`);
      return { ok: true, state, notes };
    }

    case "add_caption": {
      const text = op.text.trim();
      if (!text || text.length > 120)
        return { ok: false, error: err("INVALID_CAPTION", "Caption text must be 1–120 characters.") };
      const ws = op.wordIds.map((id) => words.get(id));
      if (!ws.length || ws.some((w) => !w))
        return { ok: false, error: err("UNKNOWN_WORD", "Caption must anchor to existing transcript words.") };
      const ids = (ws as Word[]).sort((a, b) => a.index - b.index).map((w) => w.id);
      if (state.captions.some((c) => c.text === text && c.wordIds.join() === ids.join()))
        return { ok: false, error: err("NO_CHANGE", "An identical caption already exists on those words.") };
      state.captions.push({ id: `cap_${state.nextId++}`, text, wordIds: ids, size: "md", position: "bottom" });
      const placed = layout(state.segments);
      const hidden = (ws as Word[]).filter((w) => !placeWord(placed, w).visible).length;
      if (hidden) notes.push(`${hidden} anchored word(s) are currently cut; the caption shows over the rest.`);
      return { ok: true, state, notes };
    }

    case "remove_caption": {
      const before = state.captions.length;
      state.captions = state.captions.filter((c) => c.id !== op.captionId);
      if (state.captions.length === before)
        return { ok: false, error: err("UNKNOWN_CAPTION", "That caption does not exist.", { captionId: op.captionId }) };
      return { ok: true, state, notes };
    }

    case "style_caption": {
      const cap = state.captions.find((c) => c.id === op.captionId);
      if (!cap) return { ok: false, error: err("UNKNOWN_CAPTION", "That caption does not exist.") };
      let changed = false;
      if (op.sizeStep) {
        const i = SIZES.indexOf(cap.size) + op.sizeStep;
        if (i >= 0 && i < SIZES.length) {
          cap.size = SIZES[i];
          changed = true;
        }
      }
      if (op.position && op.position !== cap.position) {
        cap.position = op.position;
        changed = true;
      }
      if (!changed) return { ok: false, error: err("NO_CHANGE", "The caption already has that style.") };
      return { ok: true, state, notes };
    }

    case "move": {
      const e = validateRange(op.range, state);
      if (e) return { ok: false, error: e };
      let segs = splitAt(splitAt(state.segments, op.range.start), op.range.end);
      const moving = segs.filter((s) => s.srcStart >= op.range.start && s.srcEnd <= op.range.end);
      if (!moving.length)
        return { ok: false, error: err("NO_VISIBLE_CONTENT", "That range is entirely cut, nothing to move.") };
      let rest = segs.filter((s) => !(s.srcStart >= op.range.start && s.srcEnd <= op.range.end));
      if (!rest.length) return { ok: false, error: err("NO_CHANGE", "Moving the whole video changes nothing.") };
      let insertAt: number;
      if (op.to === "start") insertAt = 0;
      else if (op.to === "end") insertAt = rest.length;
      else {
        const t = op.to.beforeSrc;
        if (t > op.range.start && t < op.range.end)
          return { ok: false, error: err("INVALID_MOVE", "The destination lies inside the section being moved.") };
        rest = splitAt(rest, t);
        insertAt = rest.findIndex((s) => s.srcStart === t);
        if (insertAt < 0) {
          const endIdx = rest.findIndex((s) => s.srcEnd === t);
          if (endIdx < 0)
            return { ok: false, error: err("DESTINATION_CUT", "The destination time is not in the current timeline.") };
          insertAt = endIdx + 1;
        }
      }
      segs = [...rest.slice(0, insertAt), ...moving, ...rest.slice(insertAt)];
      const next = normalizeSegments(segs);
      if (JSON.stringify(next) === JSON.stringify(state.segments))
        return { ok: false, error: err("NO_CHANGE", "The section is already at that position.") };
      state.segments = next;
      return { ok: true, state, notes };
    }
  }
}

/** Applies all ops or none (no partial multi-operation edits). */
export function applyOps(
  state: TimelineState,
  ops: Op[],
  words: Map<string, Word>,
): { ok: true; state: TimelineState; notes: string[] } | { ok: false; error: EngineError; failedIndex: number } {
  let cur = state;
  const notes: string[] = [];
  for (let i = 0; i < ops.length; i++) {
    const r = applyOp(cur, ops[i], words);
    if (!r.ok) return { ok: false, error: r.error, failedIndex: i };
    cur = r.state;
    notes.push(...r.notes);
  }
  return { ok: true, state: cur, notes };
}
