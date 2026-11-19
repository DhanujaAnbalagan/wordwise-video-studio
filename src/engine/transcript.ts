import type { Interval, Transcript, Word } from "./types";

export const LOW_CONFIDENCE = 0.6;

export class TranscriptImportError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

type RawWord = Record<string, unknown>;

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

/**
 * Accepts VideoFlow transcript JSON ({ words: [{ text, start, end, ... }] }, times in seconds)
 * or Whisper verbose_json ({ segments: [{ words: [{ word, start, end, probability }] }] }).
 * Speaker and confidence are copied only when present — never invented.
 */
export function importTranscript(json: unknown, fallbackDurationMs?: number): Transcript {
  if (!json || typeof json !== "object") throw new TranscriptImportError("INVALID_TRANSCRIPT", "Transcript must be a JSON object.");
  const obj = json as Record<string, unknown>;
  let raw: RawWord[] = [];
  let provider = "imported";
  let model: string | undefined;
  let note: string | undefined;
  if (Array.isArray(obj.words)) raw = obj.words as RawWord[];
  else if (Array.isArray(obj.segments)) {
    provider = "whisper-json";
    raw = (obj.segments as Record<string, unknown>[]).flatMap((s) =>
      Array.isArray(s.words)
        ? (s.words as RawWord[]).map((w) => ({ ...w, speaker: w.speaker ?? s.speaker }))
        : [],
    );
  }
  if (!raw.length) throw new TranscriptImportError("INVALID_TRANSCRIPT", "No word-level timestamps found (expected `words` or `segments[].words`).");
  const src = obj.source as Record<string, unknown> | undefined;
  if (src) {
    if (typeof src.provider === "string") provider = src.provider;
    if (typeof src.model === "string") model = src.model;
    if (typeof src.note === "string") note = src.note;
  }
  const words: Word[] = raw.map((w, i) => {
    const text = String(w.text ?? w.word ?? "").trim();
    const start = num(w.start);
    const end = num(w.end);
    if (!text || start == null || end == null || end < start)
      throw new TranscriptImportError("INVALID_TRANSCRIPT", `Word #${i + 1} is missing text or valid start/end times.`);
    const conf = num(w.confidence) ?? num(w.probability);
    const spk = w.speaker_id ?? w.speaker;
    return {
      id: typeof w.word_id === "string" ? w.word_id : `w_${String(i + 1).padStart(4, "0")}`,
      index: i,
      text,
      start: Math.round(start * 1000),
      end: Math.max(Math.round(end * 1000), Math.round(start * 1000) + 1),
      ...(typeof spk === "string" ? { speaker: spk } : {}),
      ...(conf != null ? { confidence: conf } : {}),
    };
  });
  words.sort((a, b) => a.start - b.start || a.index - b.index).forEach((w, i) => (w.index = i));
  const ids = new Set(words.map((w) => w.id));
  if (ids.size !== words.length) throw new TranscriptImportError("INVALID_TRANSCRIPT", "Word IDs must be unique.");
  const speakers: Record<string, { label: string }> = {};
  const rawSpk = obj.speakers as Record<string, { label?: string }> | undefined;
  for (const w of words) if (w.speaker && !speakers[w.speaker]) speakers[w.speaker] = { label: rawSpk?.[w.speaker]?.label ?? w.speaker };
  const durationMs = Math.round((num(obj.duration) ?? 0) * 1000) || fallbackDurationMs || words[words.length - 1].end;
  return { provider, model, note, durationMs, speakers, words };
}

export function normToken(t: string): string {
  return t.toLowerCase().replace(/[’]/g, "'").replace(/[^a-z0-9']/g, "");
}

export function endsSentence(t: string): boolean {
  return /[.?!]["')\]]*$/.test(t);
}

/** Sentences as [firstIndex, lastIndex] word index pairs; also split on speaker change. */
export function sentences(words: Word[]): [number, number][] {
  const out: [number, number][] = [];
  let start = 0;
  words.forEach((w, i) => {
    const next = words[i + 1];
    if (endsSentence(w.text) || !next || (next.speaker ?? "") !== (w.speaker ?? "")) {
      out.push([start, i]);
      start = i + 1;
    }
  });
  return out;
}

export function sentenceOf(words: Word[], index: number): [number, number] {
  return sentences(words).find(([a, b]) => index >= a && index <= b) ?? [index, index];
}

export interface PhraseMatch {
  wordIds: string[];
  start: number;
  end: number;
  firstIndex: number;
  lastIndex: number;
  kind: "exact" | "related";
  matched: string;
}

const RELATED: string[][] = [
  ["pricing", "price", "prices", "priced", "cost", "costs", "charge", "charged", "fee", "fees", "dollars", "pay", "paid", "plan", "plans"],
  ["introduction", "intro", "welcome", "begin", "start"],
  ["customer", "customers", "client", "clients", "teachers", "users"],
  ["confidential", "secret", "private"],
  ["thanks", "thank", "grateful"],
];

export function relatedTerms(term: string): string[] {
  const t = normToken(term);
  const group = RELATED.find((g) => g.includes(t));
  return group ? group.filter((x) => x !== t) : [];
}

/** Exact phrase search, plus related single-word matches when `includeRelated`. */
export function searchPhrase(words: Word[], phrase: string, includeRelated = false): PhraseMatch[] {
  const toks = phrase.split(/\s+/).map(normToken).filter(Boolean);
  if (!toks.length) return [];
  const out: PhraseMatch[] = [];
  for (let i = 0; i + toks.length <= words.length; i++) {
    let ok = true;
    for (let j = 0; j < toks.length; j++) if (normToken(words[i + j].text) !== toks[j]) { ok = false; break; }
    if (ok) {
      const ws = words.slice(i, i + toks.length);
      out.push({ wordIds: ws.map((w) => w.id), start: ws[0].start, end: ws[ws.length - 1].end, firstIndex: i, lastIndex: i + toks.length - 1, kind: "exact", matched: ws.map((w) => w.text).join(" ") });
    }
  }
  if (includeRelated && toks.length === 1) {
    const rel = new Set(relatedTerms(toks[0]));
    words.forEach((w, i) => {
      if (rel.has(normToken(w.text)))
        out.push({ wordIds: [w.id], start: w.start, end: w.end, firstIndex: i, lastIndex: i, kind: "related", matched: w.text });
    });
  }
  return out.sort((a, b) => a.start - b.start);
}

export function contextOf(words: Word[], first: number, last: number, pad = 4): string {
  const a = Math.max(0, first - pad);
  const b = Math.min(words.length - 1, last + pad);
  return `${a > 0 ? "…" : ""}${words.slice(a, b + 1).map((w) => w.text).join(" ")}${b < words.length - 1 ? "…" : ""}`;
}

const ALWAYS_FILLERS = new Set(["um", "uh", "uhm", "umm", "erm", "er", "ah", "hmm", "mm"]);

/**
 * Filler rule: um/uh/erm/er/ah/hmm are always fillers. "like" counts only when set off by
 * commas on both sides ("it was, like, great"); "I like this topic" is content and is kept.
 */
export function isFiller(words: Word[], i: number): boolean {
  const t = normToken(words[i].text);
  if (ALWAYS_FILLERS.has(t)) return true;
  if (t === "like") return /,$/.test(words[i].text) && i > 0 && /,$/.test(words[i - 1].text);
  return false;
}

const LEAD_IN = new Set(["so", "um", "uh", "and", "well", "okay", "ok", "right", "now"]);

/** Sentence-initial discourse markers ("So, uh, ...") as word index runs. */
export function leadIns(words: Word[]): { indices: number[]; speaker?: string }[] {
  const out: { indices: number[]; speaker?: string }[] = [];
  for (const [a, b] of sentences(words)) {
    const idx: number[] = [];
    for (let i = a; i < b; i++) {
      const raw = words[i].text;
      if (LEAD_IN.has(normToken(raw)) && (/,$/.test(raw) || ALWAYS_FILLERS.has(normToken(raw)))) idx.push(i);
      else break;
    }
    if (idx.length) out.push({ indices: idx, speaker: words[a].speaker });
  }
  return out;
}

export interface TranscriptWarning {
  kind: "low_confidence" | "overlap" | "speaker_swap";
  message: string;
  wordIds: string[];
}

export function transcriptWarnings(t: Transcript): TranscriptWarning[] {
  const out: TranscriptWarning[] = [];
  const w = t.words;
  for (const x of w)
    if (x.confidence != null && x.confidence < LOW_CONFIDENCE)
      out.push({ kind: "low_confidence", message: `Low confidence (${Math.round(x.confidence * 100)}%) for “${x.text}”`, wordIds: [x.id] });
  for (let i = 1; i < w.length; i++) {
    const a = w[i - 1];
    const b = w[i];
    if (b.start < a.end && a.speaker && b.speaker && a.speaker !== b.speaker)
      out.push({ kind: "overlap", message: `Overlapping speech between ${a.speaker} and ${b.speaker} near “${a.text} / ${b.text}”`, wordIds: [a.id, b.id] });
  }
  for (let i = 1; i + 1 < w.length; i++) {
    const [a, b, c] = [w[i - 1], w[i], w[i + 1]];
    if (a.speaker && b.speaker && c.speaker && a.speaker === c.speaker && b.speaker !== a.speaker && b.start - a.end < 300 && c.start - b.end < 300)
      out.push({ kind: "speaker_swap", message: `Possible speaker-label swap: “${b.text}” is labelled ${b.speaker} between ${a.speaker} words`, wordIds: [b.id] });
  }
  return out;
}

export function wordsInRange(words: Word[], r: Interval): Word[] {
  return words.filter((w) => {
    const ov = Math.min(w.end, r.end) - Math.max(w.start, r.start);
    return ov > 0 && ov >= (w.end - w.start) * 0.5;
  });
}

/**
 * Word-safe boundaries: a cut boundary that falls inside a word moves to the nearer edge of
 * that word, so cuts never clip half a word. Returns the adjusted range and what moved.
 */
export function snapToWords(words: Word[], r: Interval): { range: Interval; moved: string[] } {
  const moved: string[] = [];
  const snap = (t: number, label: string) => {
    const w = words.find((x) => x.start < t && t < x.end);
    if (!w) return t;
    const to = t - w.start <= w.end - t ? w.start : w.end;
    moved.push(`${label} moved from ${(t / 1000).toFixed(3)}s to ${(to / 1000).toFixed(3)}s to avoid clipping “${w.text}”`);
    return to;
  };
  return { range: { start: snap(r.start, "Start"), end: snap(r.end, "End") }, moved };
}
