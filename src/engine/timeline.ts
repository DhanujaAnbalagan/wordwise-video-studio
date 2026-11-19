import { stableHash } from "./hash";
import type {
  AudioEffect,
  Caption,
  Interval,
  Segment,
  StateSummary,
  TimelineState,
  Word,
} from "./types";

/** Minimum readable caption display duration in timeline ms (spec: ~0.7s). */
export const MIN_CAPTION_MS = 700;
/** A word counts as kept when at least this fraction of its source span is still played. */
export const WORD_KEEP_FRACTION = 0.5;

export function initialState(sourceDurationMs: number): TimelineState {
  return {
    sourceDurationMs,
    segments: [{ srcStart: 0, srcEnd: sourceDurationMs, speed: 1 }],
    captions: [],
    effects: [],
    nextId: 1,
  };
}

/** Canonical form: drops empty pieces and merges source-contiguous neighbours of equal speed. */
export function normalizeSegments(segs: Segment[]): Segment[] {
  const out: Segment[] = [];
  for (const s of segs) {
    if (s.srcEnd - s.srcStart < 1) continue;
    const prev = out[out.length - 1];
    if (prev && prev.srcEnd === s.srcStart && prev.speed === s.speed) {
      out[out.length - 1] = { ...prev, srcEnd: s.srcEnd };
    } else {
      out.push({ ...s });
    }
  }
  return out;
}

export function splitAt(segs: Segment[], t: number): Segment[] {
  return segs.flatMap((s) =>
    s.srcStart < t && t < s.srcEnd
      ? [
          { ...s, srcEnd: t },
          { ...s, srcStart: t },
        ]
      : [s],
  );
}

export function subtractRange(segs: Segment[], r: Interval): Segment[] {
  return segs.flatMap((s) => {
    if (r.end <= s.srcStart || r.start >= s.srcEnd) return [s];
    const out: Segment[] = [];
    if (s.srcStart < r.start) out.push({ ...s, srcEnd: r.start });
    if (r.end < s.srcEnd) out.push({ ...s, srcStart: r.end });
    return out;
  });
}

export function mergeIntervals(list: Interval[]): Interval[] {
  const sorted = [...list].filter((i) => i.end > i.start).sort((a, b) => a.start - b.start);
  const out: Interval[] = [];
  for (const i of sorted) {
    const prev = out[out.length - 1];
    if (prev && i.start <= prev.end) prev.end = Math.max(prev.end, i.end);
    else out.push({ ...i });
  }
  return out;
}

/** Source intervals that are currently played somewhere in the timeline. */
export function presentIntervals(segs: Segment[]): Interval[] {
  return mergeIntervals(segs.map((s) => ({ start: s.srcStart, end: s.srcEnd })));
}

export function intervalMinus(r: Interval, list: Interval[]): Interval[] {
  let pieces: Interval[] = [{ ...r }];
  for (const x of list) {
    pieces = pieces.flatMap((p) => {
      if (x.end <= p.start || x.start >= p.end) return [p];
      const out: Interval[] = [];
      if (p.start < x.start) out.push({ start: p.start, end: x.start });
      if (x.end < p.end) out.push({ start: x.end, end: p.end });
      return out;
    });
  }
  return pieces.filter((p) => p.end > p.start);
}

export function overlap(a: Interval, b: Interval): number {
  return Math.max(0, Math.min(a.end, b.end) - Math.max(a.start, b.start));
}

export function presentMs(segs: Segment[], r: Interval): number {
  return presentIntervals(segs).reduce((acc, p) => acc + overlap(p, r), 0);
}

export interface PlacedSegment extends Segment {
  index: number;
  tlStart: number;
  tlEnd: number;
}

export function layout(segs: Segment[]): PlacedSegment[] {
  let t = 0;
  return segs.map((s, index) => {
    const d = (s.srcEnd - s.srcStart) / s.speed;
    const p = { ...s, index, tlStart: t, tlEnd: t + d };
    t += d;
    return p;
  });
}

export function timelineDuration(segs: Segment[]): number {
  return segs.reduce((acc, s) => acc + (s.srcEnd - s.srcStart) / s.speed, 0);
}

/** Source time -> current timeline time; null when that source moment is cut. */
export function srcToTl(placed: PlacedSegment[], t: number): number | null {
  for (const p of placed) {
    if (p.srcStart <= t && t < p.srcEnd) return p.tlStart + (t - p.srcStart) / p.speed;
  }
  for (const p of placed) if (t === p.srcEnd) return p.tlEnd;
  return null;
}

/** Current timeline time -> source time and the playing segment index. */
export function tlToSrc(
  placed: PlacedSegment[],
  x: number,
): { src: number; segIndex: number } | null {
  if (!placed.length) return null;
  const total = placed[placed.length - 1].tlEnd;
  const c = Math.min(Math.max(x, 0), total);
  for (const p of placed) {
    if (p.tlStart <= c && c < p.tlEnd) {
      return { src: p.srcStart + (c - p.tlStart) * p.speed, segIndex: p.index };
    }
  }
  const last = placed[placed.length - 1];
  return { src: last.srcEnd, segIndex: last.index };
}

/** A current-timeline range expressed as the (possibly several) source intervals it plays. */
export function tlRangeToSrc(placed: PlacedSegment[], a: number, b: number): Interval[] {
  const out: Interval[] = [];
  for (const p of placed) {
    const s = Math.max(a, p.tlStart);
    const e = Math.min(b, p.tlEnd);
    if (e > s) {
      out.push({
        start: Math.round(p.srcStart + (s - p.tlStart) * p.speed),
        end: Math.round(p.srcStart + (e - p.tlStart) * p.speed),
      });
    }
  }
  return out;
}

export interface WordPlacement {
  visible: boolean;
  keptFraction: number;
  tlStart: number | null;
  tlEnd: number | null;
  speed: number | null;
}

export function placeWord(placed: PlacedSegment[], w: Word): WordPlacement {
  const dur = Math.max(1, w.end - w.start);
  let kept = 0;
  let best: { p: PlacedSegment; s: number; e: number } | null = null;
  for (const p of placed) {
    const s = Math.max(w.start, p.srcStart);
    const e = Math.min(w.end, p.srcEnd);
    if (e > s) {
      kept += e - s;
      if (!best || e - s > best.e - best.s) best = { p, s, e };
    }
  }
  const keptFraction = kept / dur;
  if (!best) return { visible: false, keptFraction: 0, tlStart: null, tlEnd: null, speed: null };
  const { p, s, e } = best;
  return {
    visible: keptFraction >= WORD_KEEP_FRACTION,
    keptFraction,
    tlStart: p.tlStart + (s - p.srcStart) / p.speed,
    tlEnd: p.tlStart + (e - p.srcStart) / p.speed,
    speed: p.speed,
  };
}

export function placeWords(placed: PlacedSegment[], words: Word[]): Map<string, WordPlacement> {
  const m = new Map<string, WordPlacement>();
  for (const w of words) m.set(w.id, placeWord(placed, w));
  return m;
}

export interface CaptionDisplay {
  caption: Caption;
  status: "visible" | "partial" | "removed";
  keptWords: number;
  totalWords: number;
  anchor: Interval;
  spans: { tlStart: number; tlEnd: number; extended: boolean; minReached: boolean }[];
}

/**
 * Caption rule: a caption is shown over its surviving anchored words in current timeline time.
 * - All anchored words cut -> status "removed" (hidden, kept in state so restores bring it back).
 * - Some words cut -> status "partial": shown over the surviving words, text unchanged.
 * - Surviving words that are not adjacent in the timeline (after a reorder) form separate spans.
 * - Spans shorter than MIN_CAPTION_MS are extended up to the next caption or timeline end.
 */
export function resolveCaptions(
  state: TimelineState,
  wordsById: Map<string, Word>,
  placements?: Map<string, WordPlacement>,
): CaptionDisplay[] {
  const placed = layout(state.segments);
  const total = placed.length ? placed[placed.length - 1].tlEnd : 0;
  const out: CaptionDisplay[] = state.captions.map((caption) => {
    const words = caption.wordIds.map((id) => wordsById.get(id)).filter(Boolean) as Word[];
    const anchor = {
      start: Math.min(...words.map((w) => w.start)),
      end: Math.max(...words.map((w) => w.end)),
    };
    const vis = words
      .map((w) => ({ w, p: placements?.get(w.id) ?? placeWord(placed, w) }))
      .filter((x) => x.p.visible)
      .sort((a, b) => (a.p.tlStart ?? 0) - (b.p.tlStart ?? 0));
    const spans: CaptionDisplay["spans"] = [];
    for (const { p } of vis) {
      const last = spans[spans.length - 1];
      if (last && (p.tlStart ?? 0) - last.tlEnd <= 400) last.tlEnd = Math.max(last.tlEnd, p.tlEnd ?? 0);
      else spans.push({ tlStart: p.tlStart ?? 0, tlEnd: p.tlEnd ?? 0, extended: false, minReached: true });
    }
    return {
      caption,
      status: vis.length === 0 ? "removed" : vis.length < words.length ? "partial" : "visible",
      keptWords: vis.length,
      totalWords: words.length,
      anchor,
      spans,
    };
  });
  const all = out.flatMap((d) => d.spans).sort((a, b) => a.tlStart - b.tlStart);
  all.forEach((s, i) => {
    if (s.tlEnd - s.tlStart >= MIN_CAPTION_MS) return;
    const limit = Math.min(i + 1 < all.length ? all[i + 1].tlStart : total, total);
    const target = s.tlStart + MIN_CAPTION_MS;
    const newEnd = Math.max(s.tlEnd, Math.min(target, limit));
    s.extended = newEnd > s.tlEnd;
    s.minReached = newEnd >= target - 0.5;
    s.tlEnd = newEnd;
  });
  return out;
}

export interface PlacedEffect {
  effect: AudioEffect;
  tlStart: number;
  tlEnd: number;
}

export function placeEffects(state: TimelineState): PlacedEffect[] {
  const placed = layout(state.segments);
  const out: PlacedEffect[] = [];
  for (const effect of state.effects) {
    for (const p of placed) {
      const s = Math.max(effect.srcStart, p.srcStart);
      const e = Math.min(effect.srcEnd, p.srcEnd);
      if (e > s) {
        out.push({
          effect,
          tlStart: p.tlStart + (s - p.srcStart) / p.speed,
          tlEnd: p.tlStart + (e - p.srcStart) / p.speed,
        });
      }
    }
  }
  return out.sort((a, b) => a.tlStart - b.tlStart || a.effect.id.localeCompare(b.effect.id));
}

/** Identity of the edit, excluding counters. Same content -> same hash, on every run. */
export function timelineHash(state: TimelineState): string {
  return stableHash({
    sourceDurationMs: state.sourceDurationMs,
    segments: state.segments,
    captions: state.captions,
    effects: [...state.effects].sort((a, b) => a.srcStart - b.srcStart || a.kind.localeCompare(b.kind)),
  });
}

export function summarize(state: TimelineState, words: Word[]): StateSummary {
  const placed = layout(state.segments);
  const byId = new Map(words.map((w) => [w.id, w]));
  const placements = placeWords(placed, words);
  const caps = resolveCaptions(state, byId, placements);
  return {
    durationTl: timelineDuration(state.segments),
    segments: state.segments.length,
    visibleWords: [...placements.values()].filter((p) => p.visible).length,
    captionsVisible: caps.filter((c) => c.status !== "removed").length,
    captionsHidden: caps.filter((c) => c.status === "removed").length,
    effects: state.effects.length,
    hash: timelineHash(state),
  };
}
