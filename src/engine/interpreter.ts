/**
 * Deterministic rule-based instruction interpreter.
 * It only TRANSLATES text into structured operations (or a clarification / rejection).
 * It never mutates the timeline — the executor validates and applies operations.
 *
 * Time policy: timestamps in instructions are ORIGINAL SOURCE time, unless the instruction
 * says "current", "edited", "timeline" or "output", in which case they are CURRENT TIMELINE time.
 */
import { formatTc, parseTime, TIME_PATTERN } from "./time";
import { layout, placeWords, srcToTl, timelineDuration, tlRangeToSrc, presentIntervals } from "./timeline";
import type { WordPlacement } from "./timeline";
import { intervalMinus } from "./timeline";
import {
  contextOf,
  isFiller,
  leadIns,
  normToken,
  relatedTerms,
  searchPhrase,
  sentenceOf,
  snapToWords,
  wordsInRange,
  type PhraseMatch,
} from "./transcript";
import type { ClarificationOption, Interval, Op, TimelineState, Transcript, Word } from "./types";

export interface InterpretContext {
  transcript: Transcript;
  state: TimelineState;
  selection: string[];
  selectedCaptionId: string | null;
  playheadSrcMs?: number;
}

export type Interpretation =
  | { kind: "ops"; ops: Op[]; notes: string[] }
  | { kind: "clarify"; question: string; options: ClarificationOption[]; notes: string[] }
  | { kind: "reject"; code: string; message: string; details?: Record<string, unknown> }
  | { kind: "undo" }
  | { kind: "redo" }
  | { kind: "restore_revision"; revisionId: string };

export const SUPPORTED_EXAMPLES = [
  "Remove the first 10 seconds",
  "Cut the section from 00:30 to 00:45",
  "Remove everything before the introduction",
  "Make the section after 1:20 play at 2x speed",
  "Mute the word confidential",
  "Bleep the selected words",
  'Add caption "Welcome!" to words at 42-44',
  "Remove filler words",
  "Remove silences longer than 2 seconds",
  "Move the selected section to the start",
  "Remove the pricing section",
  "Restore the introduction",
  "Undo / Redo / Restore revision 2",
];

const T = TIME_PATTERN;
const RANGE_RE = new RegExp(`(?:from|between)?\\s*${T}\\s*(?:to|-|–|and|until|through)\\s*${T}`);
const SILENCE_THRESHOLD_MAX_DELIBERATE = 4000;
const SILENCE_PAD = 250;

interface Ctx extends InterpretContext {
  words: Word[];
  byId: Map<string, Word>;
  placements: Map<string, WordPlacement>;
  visible: (w: Word) => boolean;
  dur: number;
  tlDur: number;
}

const ops = (o: Op[], notes: string[] = []): Interpretation => ({ kind: "ops", ops: o, notes });
const reject = (code: string, message: string, details?: Record<string, unknown>): Interpretation => ({
  kind: "reject",
  code,
  message,
  details,
});

function fmtRange(r: Interval) {
  return `${formatTc(r.start)}–${formatTc(r.end)}`;
}

function detectBasis(t: string): { text: string; basis: "source" | "timeline" } {
  const re = /\s*\b(?:(?:in|of|on)\s+)?(?:the\s+)?(?:current|edited|output)(?:\s+(?:timeline|video|edit))?(?:\s+time)?\b|\s*\(?\btimeline time\)?|\s*\bin the timeline\b/;
  if (re.test(t)) return { text: t.replace(re, " ").replace(/\s+/g, " ").trim(), basis: "timeline" };
  return { text: t.replace(/\s*\(?\b(?:original|source)(?: source)?(?: time)?\)?/g, " ").replace(/\s+/g, " ").trim(), basis: "source" };
}

/** Converts a user range to source intervals, validating bounds per the time policy. */
function withRange(
  c: Ctx,
  a: number,
  b: number,
  basis: "source" | "timeline",
  build: (ranges: Interval[], notes: string[]) => Interpretation,
): Interpretation {
  const limit = basis === "source" ? c.dur : Math.floor(c.tlDur);
  const label = basis === "source" ? "original source time" : "current timeline time";
  if (b <= a) return reject("INVALID_RANGE", `The range ${formatTc(a)}–${formatTc(b)} ends before it starts.`);
  if (a >= limit)
    return reject("RANGE_OUT_OF_BOUNDS", `${formatTc(a)} is beyond the end of the video (${formatTc(limit)} in ${label}).`, {
      start: a,
      limit,
    });
  const finish = (end: number, extra: string[]): Interpretation => {
    const ranges = basis === "source" ? [{ start: a, end }] : tlRangeToSrc(layout(c.state.segments), a, end);
    const notes = [
      `Interpreted ${formatTc(a)}–${formatTc(end)} as ${label} (time policy).`,
      ...(basis === "timeline" ? [`That maps to source ${ranges.map(fmtRange).join(", ")}.`] : []),
      ...extra,
    ];
    return build(ranges, notes);
  };
  if (b > limit) {
    const inner = finish(limit, [`End clamped from ${formatTc(b)} to the video end ${formatTc(limit)}.`]);
    if (inner.kind !== "ops") return inner;
    return {
      kind: "clarify",
      question: `${formatTc(b)} is past the end of the video (${formatTc(limit)} in ${label}). Use the video end instead?`,
      options: [{ label: `Use ${formatTc(a)}–${formatTc(limit)}`, detail: "Clamp the range to the video end", ops: inner.ops }],
      notes: inner.notes,
    };
  }
  return finish(b, []);
}

function snapRanges(c: Ctx, ranges: Interval[]) {
  const notes: string[] = [];
  const out = ranges.map((r) => {
    const s = snapToWords(c.words, r);
    notes.push(...s.moved);
    return s.range;
  });
  return { ranges: out.filter((r) => r.end > r.start), notes };
}

function selectionRange(c: Ctx): Interval | null {
  const ws = c.selection.map((id) => c.byId.get(id)).filter(Boolean) as Word[];
  if (!ws.length) return null;
  return { start: Math.min(...ws.map((w) => w.start)), end: Math.max(...ws.map((w) => w.end)) };
}

function sentenceRange(c: Ctx, m: PhraseMatch): Interval {
  const [a] = sentenceOf(c.words, m.firstIndex);
  const [, b] = sentenceOf(c.words, m.lastIndex);
  return { start: c.words[a].start, end: c.words[b].end };
}

function extractSpeaker(c: Ctx, t: string): { text: string; speaker?: string; error?: Interpretation } {
  const m = t.match(/\s*\b(?:from |by |of |for |said by )?(?:the )?(host|interviewer|guest|interviewee|speaker\s*(\d+)|s(\d+))(?:'s|’s)?\b/);
  if (!m) return { text: t };
  const spk = Object.entries(c.transcript.speakers);
  let id: string | undefined;
  const key = m[1];
  if (/host|interviewer/.test(key)) id = spk.find(([, v]) => /host|interviewer/i.test(v.label))?.[0];
  else if (/guest|interviewee/.test(key)) id = spk.find(([, v]) => /guest|interviewee/i.test(v.label))?.[0];
  else id = `S${m[2] ?? m[3]}`;
  if (!id || !c.transcript.speakers[id])
    return { text: t, error: reject("UNKNOWN_SPEAKER", `This transcript has no speaker matching “${key}”.`, { speakers: Object.keys(c.transcript.speakers) }) };
  return { text: t.replace(m[0], " ").replace(/\s+/g, " ").trim(), speaker: id };
}

function speakerLabel(c: Ctx, id?: string) {
  return id ? (c.transcript.speakers[id]?.label ?? id) : "";
}

function cleanPhrase(p: string): string {
  return p
    .replace(/^["'“‘]|["'”’]$/g, "")
    .replace(/^(?:the|a|an)\s+/, "")
    .replace(/\s+(?:section|part|segment|bit|sentence|mentions?|occurrences?)$/, "")
    .trim();
}

/** Destructive phrase targeting: shows every match and asks unless exactly one exists. */
function targetPhrase(
  c: Ctx,
  phrase: string,
  unit: "word" | "sentence",
  speaker: string | undefined,
  build: (r: Interval) => Op[],
  verb: string,
): Interpretation {
  const all = searchPhrase(c.words, phrase).filter((m) => m.wordIds.every((id) => c.visible(c.byId.get(id)!)));
  const matches = speaker ? all.filter((m) => c.byId.get(m.wordIds[0])!.speaker === speaker) : all;
  const rangeOf = (m: PhraseMatch) => (unit === "sentence" ? sentenceRange(c, m) : { start: m.start, end: m.end });
  const desc = (m: PhraseMatch) => {
    const r = rangeOf(m);
    const spk = c.byId.get(m.wordIds[0])!.speaker;
    return `${spk && Object.keys(c.transcript.speakers).length > 1 ? `${speakerLabel(c, spk)} · ` : ""}source ${fmtRange(r)} — ${contextOf(c.words, m.firstIndex, m.lastIndex)}`;
  };
  if (!matches.length) {
    const related = searchPhrase(c.words, phrase, true).filter((m) => m.kind === "related" && c.visible(c.byId.get(m.wordIds[0])!));
    if (related.length)
      return {
        kind: "clarify",
        question: `No exact mention of “${phrase}”${speaker ? ` by ${speakerLabel(c, speaker)}` : ""}. I found ${related.length} related mention(s). Which one should I ${verb}?`,
        options: related.map((m, i) => ({ label: `Related ${i + 1}: “${m.matched}”`, detail: desc(m), ops: build(rangeOf(m)) })),
        notes: [`Related terms searched: ${relatedTerms(phrase).join(", ")}.`],
      };
    return reject("NOT_FOUND", `I couldn't find “${phrase}”${speaker ? ` spoken by ${speakerLabel(c, speaker)}` : ""} in the current timeline.`);
  }
  if (matches.length === 1) return ops(build(rangeOf(matches[0])), [`Matched “${phrase}” once: ${desc(matches[0])}.`]);
  const options: ClarificationOption[] = matches.map((m, i) => ({ label: `Match ${i + 1}`, detail: desc(m), ops: build(rangeOf(m)) }));
  options.push({ label: `All ${matches.length} matches`, detail: `${verb} every match`, ops: matches.flatMap((m) => build(rangeOf(m))) });
  return {
    kind: "clarify",
    question: `“${phrase}” is mentioned ${matches.length} times. Which one should I ${verb}?`,
    options,
    notes: ["No change was made yet: destructive edits on ambiguous targets need your choice."],
  };
}

function wordIdsForTarget(c: Ctx, target: string, speaker?: string): { ids: string[]; note: string } | Interpretation {
  if (/^(?:the )?(?:selected(?: words?| section| text)?|selection|it|this|these words)$/.test(target)) {
    if (!c.selection.length) return { kind: "clarify", question: "Which words? Select words in the transcript first, or name the word.", options: [], notes: [] };
    return { ids: [...c.selection], note: `Using the ${c.selection.length} selected word(s).` };
  }
  const rm = target.match(RANGE_RE);
  if (rm) {
    const a = parseTime(rm[1]);
    const b = parseTime(rm[2]);
    if (a == null || b == null || b <= a) return reject("INVALID_RANGE", "That time range is not valid.");
    if (a >= c.dur) return reject("RANGE_OUT_OF_BOUNDS", `${formatTc(a)} is beyond the source duration ${formatTc(c.dur)}.`);
    const ws = wordsInRange(c.words, { start: a, end: Math.min(b, c.dur) });
    if (!ws.length) return reject("NOT_FOUND", `No words are spoken between ${formatTc(a)} and ${formatTc(b)} (source time).`);
    return { ids: ws.map((w) => w.id), note: `Interpreted ${formatTc(a)}–${formatTc(b)} as original source time (time policy): ${ws.length} word(s).` };
  }
  const phrase = cleanPhrase(target.replace(/^(?:the )?(?:word|phrase|words)\s+/, ""));
  let matches = searchPhrase(c.words, phrase);
  if (speaker) matches = matches.filter((m) => c.byId.get(m.wordIds[0])!.speaker === speaker);
  if (!matches.length) {
    const rel = relatedTerms(phrase);
    return reject("NOT_FOUND", `“${phrase}” does not appear in the transcript${speaker ? ` for ${speakerLabel(c, speaker)}` : ""}.${rel.length ? ` Related words you could try: ${rel.slice(0, 4).join(", ")}.` : ""}`);
  }
  return { ids: matches.flatMap((m) => m.wordIds), note: `Matched “${phrase}” ${matches.length} time(s); applied to every occurrence (non-destructive).` };
}

function interpretOne(c: Ctx, rawInput: string): Interpretation {
  const orig = rawInput.trim().replace(/[.!]+$/, "");
  const lower0 = orig.toLowerCase().replace(/[’]/g, "'").replace(/\s+/g, " ");
  const { text: t, basis } = detectBasis(lower0);

  if (/^(?:undo|undo (?:the |that |my )?(?:last|previous)?\s*(?:operation|edit|change|step)?)$/.test(t)) return { kind: "undo" };
  if (/^(?:redo|redo (?:the |that )?(?:last|previous)?\s*(?:operation|edit|change|step)?)$/.test(t)) return { kind: "redo" };
  const rev = t.match(/^(?:restore|revert(?: back)? to|go back to|roll back to|return to)\s+(?:the\s+)?(?:revision|rev|version|r)\s*#?\s*(\d+)$/);
  if (rev) return { kind: "restore_revision", revisionId: `r${rev[1]}` };

  // ---- Captions -------------------------------------------------------------------------
  const capText = orig.match(/caption\s+(?:saying\s+|that says\s+)?["“'‘](.+?)["”'’](?=\s|$)/i);
  if (/^add (?:a )?caption\b/.test(t)) {
    if (!capText) return reject("INVALID_CAPTION", 'Put the caption text in quotes, e.g. add caption "Welcome!" to the selected words.');
    const text = capText[1].trim();
    const rest = t.slice(t.indexOf(capText[1].toLowerCase()) + capText[1].length + 1).replace(/^["”'’\s]+/, "");
    const target = rest.replace(/^(?:to|on|for|at|over)\s+(?:the\s+)?(?:words?\s+)?(?:spoken\s+)?(?:at\s+)?/, "").trim();
    if (!target) {
      if (c.selection.length) return ops([{ type: "add_caption", text, wordIds: [...c.selection] }], [`Anchored to the ${c.selection.length} selected word(s).`]);
      const opts: ClarificationOption[] = [];
      if (c.playheadSrcMs != null) {
        const w = c.words.find((x) => x.end >= c.playheadSrcMs! && c.visible(x));
        if (w) {
          const [a, b] = sentenceOf(c.words, w.index);
          const ids = c.words.slice(a, b + 1).map((x) => x.id);
          opts.push({ label: "Sentence at the playhead", detail: contextOf(c.words, a, b, 0), ops: [{ type: "add_caption", text, wordIds: ids }] });
        }
      }
      return { kind: "clarify", question: `Which words should “${text}” attach to? Select words in the transcript, give a source time range, or pick an option.`, options: opts, notes: [] };
    }
    const r = wordIdsForTarget(c, target);
    if ("kind" in r) return r;
    return ops([{ type: "add_caption", text, wordIds: r.ids }], [r.note]);
  }
  if (/\b(?:bigger|larger|smaller|to the top|to the bottom|at the top|at the bottom)\b/.test(t) && /\b(?:it|caption|captions|text|subtitle)\b/.test(t)) {
    const sizeStep = /bigger|larger/.test(t) ? 1 : /smaller/.test(t) ? -1 : undefined;
    const position = /top/.test(t) ? "top" : /bottom/.test(t) ? "bottom" : undefined;
    const build = (id: string): Op[] => [{ type: "style_caption", captionId: id, ...(sizeStep ? { sizeStep } : {}), ...(position ? { position } : {}) } as Op];
    if (c.selectedCaptionId) return ops(build(c.selectedCaptionId), [`Applied to the selected caption ${c.selectedCaptionId}.`]);
    if (!c.state.captions.length) return reject("NO_CAPTIONS", "There are no captions yet. Add a caption first.");
    return {
      kind: "clarify",
      question: "Which caption do you mean?",
      options: c.state.captions.map((cap) => ({ label: `“${cap.text}”`, detail: `${cap.id} · ${cap.wordIds.length} anchored words`, ops: build(cap.id) })),
      notes: [],
    };
  }
  if (/^(?:remove|delete) (?:the |this |that )?(?:selected )?caption\b/.test(t)) {
    const named = capText ? c.state.captions.filter((x) => x.text.toLowerCase() === capText[1].toLowerCase()) : [];
    if (named.length === 1) return ops([{ type: "remove_caption", captionId: named[0].id }]);
    if (c.selectedCaptionId) return ops([{ type: "remove_caption", captionId: c.selectedCaptionId }]);
    if (!c.state.captions.length) return reject("NO_CAPTIONS", "There are no captions to remove.");
    return {
      kind: "clarify",
      question: "Which caption should I remove?",
      options: c.state.captions.map((cap) => ({ label: `“${cap.text}”`, detail: cap.id, ops: [{ type: "remove_caption", captionId: cap.id }] })),
      notes: [],
    };
  }

  // ---- Restore content ------------------------------------------------------------------
  const rest = t.match(/^(?:restore|bring back|recover|put back|uncut)\s+(.+)$/);
  if (rest) {
    const target = rest[1].replace(/^(?:the\s+)?(?:earlier|original|removed|cut)\s+/, "").replace(/^the\s+/, "");
    const missing = intervalMinus({ start: 0, end: c.dur }, presentIntervals(c.state.segments));
    if (/^(?:everything|all|all cuts|whole video|entire video)$/.test(target)) {
      if (!missing.length) return reject("NO_CHANGE", "Nothing is cut, so there is nothing to restore.");
      return ops([{ type: "restore", ranges: [{ start: 0, end: c.dur }] }], ["Restoring every cut range at its original position."]);
    }
    if (/^(?:intro|introduction|opening|beginning|start)$/.test(target)) {
      const firstKept = presentIntervals(c.state.segments)[0]?.start ?? c.dur;
      if (firstKept <= 0) return reject("NO_CHANGE", "The opening of the video is not cut, so there is nothing to restore.");
      return ops([{ type: "restore", ranges: [{ start: 0, end: firstKept }] }], [
        `“Introduction” = the removed opening, source ${formatTc(0)}–${formatTc(firstKept)}. Other edits are preserved.`,
      ]);
    }
    const first = target.match(new RegExp(`^first\\s+${T}`));
    if (first) {
      const n = parseTime(first[1])!;
      return withRange(c, 0, n, basis, (ranges, notes) => ops([{ type: "restore", ranges }], notes));
    }
    const rm = target.match(RANGE_RE);
    if (rm) return withRange(c, parseTime(rm[1])!, parseTime(rm[2])!, basis, (ranges, notes) => ops([{ type: "restore", ranges }], notes));
    const phrase = cleanPhrase(target.replace(/^(?:the )?(?:part|section|sentence) (?:about|with|on)\s+/, ""));
    const cutMatches = searchPhrase(c.words, phrase).filter((m) => !m.wordIds.every((id) => c.visible(c.byId.get(id)!)));
    if (!cutMatches.length) return reject("NOT_FOUND", `No cut content mentions “${phrase}”.`);
    const build = (m: PhraseMatch): Op[] => [{ type: "restore", ranges: [sentenceRange(c, m)] }];
    if (cutMatches.length === 1) return ops(build(cutMatches[0]));
    return {
      kind: "clarify",
      question: `“${phrase}” appears in ${cutMatches.length} cut sentences. Which should I restore?`,
      options: cutMatches.map((m, i) => ({ label: `Match ${i + 1}`, detail: `source ${fmtRange(sentenceRange(c, m))} — ${contextOf(c.words, m.firstIndex, m.lastIndex)}`, ops: build(m) })),
      notes: [],
    };
  }

  // ---- Speaker-scoped edits (fillers, lead-ins) -----------------------------------------
  const sp = extractSpeaker(c, t);
  if (sp.error && /filler|lead[- ]?in|um|uh/.test(t)) return sp.error;
  const ts = sp.speaker ? sp.text : t;

  if (/\b(?:remove|cut|delete|clean up|strip|drop)\b.*\b(?:filler(?:s| words?)?|ums?(?: and uhs?)?|uhs?)\b/.test(ts)) {
    const idx = c.words.map((_, i) => i).filter((i) => isFiller(c.words, i) && c.visible(c.words[i]) && (!sp.speaker || c.words[i].speaker === sp.speaker));
    if (!idx.length) return reject("NO_CHANGE", `No filler words remain${sp.speaker ? ` for ${speakerLabel(c, sp.speaker)}` : ""}.`);
    return ops([{ type: "cut", ranges: idx.map((i) => ({ start: c.words[i].start, end: c.words[i].end })), reason: "filler_words" }], [
      `Removing ${idx.length} filler word(s)${sp.speaker ? ` spoken by ${speakerLabel(c, sp.speaker)} only` : ""}: ${idx.map((i) => `“${c.words[i].text}” @${formatTc(c.words[i].start)}`).join(", ")}.`,
      "Rule: um/uh/erm/ah/hmm are fillers; “like” only when set off by commas.",
    ]);
  }

  if (/\blead[- ]?ins?\b/.test(ts) && /\b(?:remove|cut|delete|drop|trim)\b/.test(ts)) {
    const runs = leadIns(c.words).filter((r) => r.indices.every((i) => c.visible(c.words[i])));
    const build = (rs: typeof runs): Op[] => [{ type: "cut", ranges: rs.map((r) => ({ start: c.words[r.indices[0]].start, end: c.words[r.indices[r.indices.length - 1]].end })), reason: "lead_ins" }];
    const describe = (rs: typeof runs) => rs.map((r) => `“${r.indices.map((i) => c.words[i].text).join(" ")}”`).join(", ");
    if (sp.speaker) {
      const mine = runs.filter((r) => r.speaker === sp.speaker);
      if (!mine.length) return reject("NOT_FOUND", `${speakerLabel(c, sp.speaker)} has no lead-ins to remove.`);
      return ops(build(mine), [`Targeting ${speakerLabel(c, sp.speaker)} only: ${describe(mine)}.`]);
    }
    if (!runs.length) return reject("NOT_FOUND", "No sentence lead-ins (So, / Um, / Well, …) found.");
    const spks = [...new Set(runs.map((r) => r.speaker ?? ""))];
    if (spks.length > 1) {
      return {
        kind: "clarify",
        question: "Both speakers have lead-ins. Whose lead-ins should I remove?",
        options: [
          ...spks.map((s) => {
            const rs = runs.filter((r) => (r.speaker ?? "") === s);
            return { label: `${speakerLabel(c, s)} only`, detail: describe(rs), ops: build(rs) };
          }),
          { label: "Both speakers", detail: describe(runs), ops: build(runs) },
        ],
        notes: [],
      };
    }
    return ops(build(runs), [`Removing lead-ins: ${describe(runs)}.`]);
  }

  // ---- Silence --------------------------------------------------------------------------
  if (/\b(?:remove|cut|trim|delete|shorten)\b.*\b(?:silences?|pauses?|dead air|gaps?)\b/.test(t)) {
    const th = t.match(new RegExp(`(?:longer than|over|above|more than|exceeding|>)\\s*${T}`));
    const build = (thr: number): { ops: Op[]; notes: string[] } => {
      const vis = c.words.filter(c.visible);
      const present = presentIntervals(c.state.segments);
      const gaps: { r: Interval; deliberate: boolean }[] = [];
      const bounds = [{ end: 0, text: "" } as Pick<Word, "end" | "text">, ...vis, { start: c.dur } as Pick<Word, "start">];
      for (let i = 0; i + 1 < bounds.length; i++) {
        const a = (bounds[i] as Word).end;
        const b = (bounds[i + 1] as Word).start;
        if (b - a <= thr) continue;
        const inner = i > 0 && i + 1 < bounds.length - 1;
        const deliberate = inner && /[.?!]$/.test((bounds[i] as Word).text) && b - a <= SILENCE_THRESHOLD_MAX_DELIBERATE;
        gaps.push({ r: { start: a, end: b }, deliberate });
      }
      const cuts: Interval[] = [];
      const notes: string[] = [];
      for (const g of gaps) {
        if (g.deliberate) {
          notes.push(`Kept deliberate pause ${fmtRange(g.r)} (after a sentence end, under ${SILENCE_THRESHOLD_MAX_DELIBERATE / 1000}s).`);
          continue;
        }
        const r = { start: g.r.start === 0 ? 0 : g.r.start + SILENCE_PAD, end: g.r.end === c.dur ? c.dur : g.r.end - SILENCE_PAD };
        for (const p of present) {
          const s = Math.max(p.start, r.start);
          const e = Math.min(p.end, r.end);
          if (e - s > thr / 2) cuts.push({ start: s, end: e });
        }
      }
      if (!cuts.length) return { ops: [], notes };
      notes.unshift(`Removing ${cuts.length} silence(s) longer than ${thr / 1000}s, keeping ${SILENCE_PAD}ms of air around speech.`);
      return { ops: [{ type: "cut", ranges: cuts, reason: "silence" }], notes };
    };
    if (!th) {
      const opts = [500, 1000, 2000].map((thr) => ({ thr, b: build(thr) })).filter((x) => x.b.ops.length);
      if (!opts.length) return reject("NO_CHANGE", "There are no silences longer than 0.5s to remove.");
      return { kind: "clarify", question: "How long must a silence be before I remove it?", options: opts.map((x) => ({ label: `Longer than ${x.thr / 1000}s`, detail: x.b.notes[0], ops: x.b.ops })), notes: [] };
    }
    const thr = parseTime(th[1])!;
    const b = build(thr);
    if (!b.ops.length) return reject("NO_CHANGE", `No removable silences longer than ${thr / 1000}s.${b.notes.length ? " " + b.notes.join(" ") : ""}`);
    return ops(b.ops, b.notes);
  }

  // ---- Speed ----------------------------------------------------------------------------
  const factorM = t.match(/(\d+(?:\.\d+)?)\s*x\b/) ?? t.match(/\bx\s*(\d+(?:\.\d+)?)\b/);
  const named = /\b(?:double speed|twice as fast)\b/.test(t) ? 2 : /\bhalf speed\b/.test(t) ? 0.5 : null;
  if ((factorM || named) && /\b(?:speed|play|fast|slow|make|set)\b/.test(t)) {
    const speed = named ?? Number(factorM![1]);
    const build = (ranges: Interval[], notes: string[]): Interpretation =>
      ops(ranges.map((range) => ({ type: "speed", range, speed }) as Op), notes);
    const after = t.match(new RegExp(`\\bafter\\s+${T}`));
    const before = t.match(new RegExp(`\\bbefore\\s+${T}`));
    const rm = t.match(RANGE_RE);
    if (rm) return withRange(c, parseTime(rm[1])!, parseTime(rm[2])!, basis, build);
    if (after) {
      const a = parseTime(after[1])!;
      return withRange(c, a, basis === "source" ? c.dur : Math.floor(c.tlDur), basis, build);
    }
    if (before) return withRange(c, 0, parseTime(before[1])!, basis, build);
    if (/\b(?:whole|entire|everything|all of it)\b/.test(t)) return build([{ start: 0, end: c.dur }], ["Applied to the whole source."]);
    const sel = selectionRange(c);
    if (sel) return build([sel], [`Using the selection, source ${fmtRange(sel)}.`]);
    return { kind: "clarify", question: `Which section should play at ${speed}x? Select words or give a time range.`, options: [{ label: "Whole video", ops: [{ type: "speed", range: { start: 0, end: c.dur }, speed }] }], notes: [] };
  }

  // ---- Mute / bleep ---------------------------------------------------------------------
  const mb = t.match(/^(mute|bleep|silence|censor|unmute|unbleep)\s+(.+)$/);
  if (mb) {
    const type = mb[1] === "unmute" || mb[1] === "unbleep" ? "clear_audio" : mb[1] === "bleep" || mb[1] === "censor" ? "bleep" : "mute";
    const s2 = extractSpeaker(c, mb[2]);
    if (s2.error) return s2.error;
    const r = wordIdsForTarget(c, s2.text, s2.speaker);
    if ("kind" in r) return r;
    return ops([{ type, wordIds: r.ids }], [r.note]);
  }

  // ---- Move / reorder -------------------------------------------------------------------
  const mv = t.match(/^(?:move|reorder|put|place)\s+(.+?)\s+(to (?:the )?(?:start|beginning|end)|(?:before|after)\s+.+)$/);
  if (mv) {
    let range: Interval | null = null;
    const notes: string[] = [];
    const what = mv[1].replace(/^the\s+/, "");
    if (/^(?:selected(?: section| words?| part)?|selection|it|this)$/.test(what)) {
      range = selectionRange(c);
      if (!range) return { kind: "clarify", question: "Which section should I move? Select words in the transcript or give a time range.", options: [], notes: [] };
      notes.push(`Moving the selection, source ${fmtRange(range)}.`);
    } else {
      const rm = what.match(RANGE_RE);
      if (rm) {
        const a = parseTime(rm[1])!;
        const b = parseTime(rm[2])!;
        if (b <= a || a >= c.dur) return reject("INVALID_RANGE", "That section range is not valid for this source.");
        range = snapToWords(c.words, { start: a, end: Math.min(b, c.dur) }).range;
        notes.push(`Interpreted ${formatTc(a)}–${formatTc(b)} as original source time (time policy).`);
      } else {
        const phrase = cleanPhrase(what.replace(/^(?:sentence|part|section) (?:about|with)\s+/, ""));
        const ms = searchPhrase(c.words, phrase).filter((m) => c.visible(c.byId.get(m.wordIds[0])!));
        if (!ms.length) return reject("NOT_FOUND", `I couldn't find “${phrase}” to move.`);
        if (ms.length > 1) return reject("AMBIGUOUS_TARGET", `“${phrase}” appears ${ms.length} times; select the section in the transcript and say “move the selection …”.`);
        range = sentenceRange(c, ms[0]);
        notes.push(`Moving the sentence containing “${phrase}”, source ${fmtRange(range)}.`);
      }
    }
    const dest = mv[2];
    let to: "start" | "end" | { beforeSrc: number };
    if (/start|beginning/.test(dest)) to = "start";
    else if (/end$/.test(dest)) to = "end";
    else {
      const dm = dest.match(new RegExp(`^(before|after)\\s+${T}$`));
      if (dm) to = { beforeSrc: snapToWords(c.words, { start: parseTime(dm[2])!, end: parseTime(dm[2])! }).range.start };
      else {
        const isAfter = dest.startsWith("after");
        const phrase = cleanPhrase(dest.replace(/^(?:before|after)\s+/, ""));
        const ms = searchPhrase(c.words, phrase).filter((m) => c.visible(c.byId.get(m.wordIds[0])!));
        if (ms.length !== 1) return reject(ms.length ? "AMBIGUOUS_TARGET" : "NOT_FOUND", ms.length ? `“${phrase}” appears ${ms.length} times; use a time instead.` : `I couldn't find “${phrase}”.`);
        const sr = sentenceRange(c, ms[0]);
        const [, last] = sentenceOf(c.words, ms[0].lastIndex);
        to = { beforeSrc: isAfter ? (c.words[last + 1]?.start ?? sr.end) : sr.start };
      }
    }
    return ops([{ type: "move", range, to }], notes);
  }

  // ---- Cuts -----------------------------------------------------------------------------
  const isCut = /^(?:cut|remove|delete|trim|drop|lose|get rid of)\b/.test(t);
  if (isCut) {
    const body = t.replace(/^(?:cut|remove|delete|trim|drop|lose|get rid of)\s+(?:out\s+)?/, "");
    const cutBuild = (ranges: Interval[], notes: string[]): Interpretation => {
      const s = snapRanges(c, ranges);
      if (!s.ranges.length) return reject("INVALID_RANGE", "After protecting whole words, the range is empty.");
      return ops([{ type: "cut", ranges: s.ranges }], [...notes, ...s.notes]);
    };
    if (/^(?:the )?(?:selected(?: words?| section| part| text)?|selection)$/.test(body)) {
      const r = selectionRange(c);
      if (!r) return { kind: "clarify", question: "Nothing is selected. Which words should I cut? Select them in the transcript.", options: [], notes: [] };
      return ops([{ type: "cut", ranges: [r] }], [`Cutting the selection, source ${fmtRange(r)}.`]);
    }
    if (/^(?:the )?(?:whole|entire|full) (?:video|thing|clip|recording)$|^(?:everything|all of it|all)$/.test(body))
      return ops([{ type: "cut", ranges: [{ start: 0, end: c.dur }] }]);
    const first = body.match(new RegExp(`^(?:the )?first\\s+${T}`));
    if (first) return withRange(c, 0, parseTime(first[1])!, basis, cutBuild);
    const last = body.match(new RegExp(`^(?:the )?last\\s+${T}`));
    if (last) {
      const lim = basis === "source" ? c.dur : Math.floor(c.tlDur);
      const n = parseTime(last[1])!;
      return withRange(c, Math.max(0, lim - n), lim, basis, cutBuild);
    }
    const ba = body.match(/^everything\s+(before|after)\s+(.+)$/);
    if (ba) {
      const tm = ba[2].match(new RegExp(`^${T}$`));
      if (tm) {
        const x = parseTime(tm[1])!;
        const lim = basis === "source" ? c.dur : Math.floor(c.tlDur);
        return ba[1] === "before" ? withRange(c, 0, x, basis, cutBuild) : withRange(c, x, lim, basis, cutBuild);
      }
      const phrase = cleanPhrase(ba[2]);
      const isBefore = ba[1] === "before";
      return targetPhrase(c, phrase, "sentence", sp.speaker, (r) => [{ type: "cut", ranges: [isBefore ? { start: 0, end: r.start } : { start: r.end, end: c.dur }].filter((x) => x.end > x.start) }], isBefore ? "cut before" : "cut after");
    }
    const rm = body.match(RANGE_RE);
    if (rm) return withRange(c, parseTime(rm[1])!, parseTime(rm[2])!, basis, cutBuild);

    // Phrase-based cuts
    const bodyS = sp.speaker ? extractSpeaker(c, body).text : body;
    const about = bodyS.match(/^(?:the )?(?:part|section|bit|sentence|segment)s?\s+(?:about|where .+? (?:talks?|mentions?|says?)|on|with|mentioning)\s+(.+)$/);
    const sect = bodyS.match(/^(?:the )?(.+?)\s+(?:section|part|segment|bit|sentence)$/);
    const word = bodyS.match(/^(?:the |every |all )?(?:word|phrase|mentions? of|occurrences? of)\s+(.+)$/);
    if (about || sect) return targetPhrase(c, cleanPhrase((about ?? sect)![1]), "sentence", sp.speaker, (r) => [{ type: "cut", ranges: [r] }], "cut");
    if (word) return targetPhrase(c, cleanPhrase(word[1]), "word", sp.speaker, (r) => [{ type: "cut", ranges: [r] }], "cut");
    const phrase = cleanPhrase(bodyS);
    if (phrase && phrase.split(" ").length <= 6) return targetPhrase(c, phrase, "word", sp.speaker, (r) => [{ type: "cut", ranges: [r] }], "cut");
  }

  // ---- Search (non-destructive) ---------------------------------------------------------
  const find = t.match(/^(?:find|search(?: for)?|show(?: me)?|locate|where (?:is|do (?:i|we) (?:say|mention)))\s+(?:all\s+)?(?:mentions of\s+|the word\s+)?(.+?)\??$/);
  if (find) {
    const phrase = cleanPhrase(find[1]);
    const ms = searchPhrase(c.words, phrase, true);
    if (!ms.length) return reject("NOT_FOUND", `No mention of “${phrase}” or related terms.`);
    return {
      kind: "clarify",
      question: `Found ${ms.filter((m) => m.kind === "exact").length} exact and ${ms.filter((m) => m.kind === "related").length} related mention(s) of “${phrase}”. Nothing has been changed. Cut the sentence of one of them?`,
      options: ms.map((m, i) => ({
        label: `${m.kind === "exact" ? "Match" : "Related"} ${i + 1}: “${m.matched}”`,
        detail: `source ${fmtRange(sentenceRange(c, m))}${c.visible(c.byId.get(m.wordIds[0])!) ? "" : " (currently cut)"} — ${contextOf(c.words, m.firstIndex, m.lastIndex)}`,
        ops: [{ type: "cut", ranges: [sentenceRange(c, m)] } as Op],
      })),
      notes: [],
    };
  }

  return reject("UNSUPPORTED_INSTRUCTION", `I can't map that to a supported edit. Try: ${SUPPORTED_EXAMPLES.slice(0, 5).join(" · ")}.`);
}

/** Splits multi-part instructions outside quotes. */
export function splitParts(input: string): string[] {
  const quotes: string[] = [];
  const masked = input.replace(/["“][^"”]*["”]/g, (m) => {
    quotes.push(m);
    return `\u0000${quotes.length - 1}\u0000`;
  });
  return masked
    .split(/\s*;\s*|\s*,?\s+(?:and then|then)\s+|\s*,?\s+and\s+(?=(?:remove|cut|delete|mute|bleep|speed|slow|make|add|move|restore|undo|redo|trim)\b)/i)
    .map((p) => p.replace(/\u0000(\d+)\u0000/g, (_, i) => quotes[Number(i)]).trim())
    .filter(Boolean);
}

export function buildContext(ic: InterpretContext): Ctx {
  const words = ic.transcript.words;
  const placed = layout(ic.state.segments);
  const placements = placeWords(placed, words);
  return {
    ...ic,
    words,
    byId: new Map(words.map((w) => [w.id, w])),
    placements,
    visible: (w: Word) => placements.get(w.id)?.visible ?? false,
    dur: ic.state.sourceDurationMs,
    tlDur: timelineDuration(ic.state.segments),
  };
}

export function interpret(input: string, ic: InterpretContext): Interpretation & { parts?: string[] } {
  const c = buildContext(ic);
  const parts = splitParts(input);
  if (!parts.length) return reject("EMPTY_INSTRUCTION", "Type an editing instruction.");
  if (parts.length === 1) return interpretOne(c, parts[0]);
  const results = parts.map((p) => interpretOne(c, p));
  const bad = results.findIndex((r) => r.kind !== "ops");
  if (bad >= 0) {
    const r = results[bad];
    if (r.kind === "undo" || r.kind === "redo" || r.kind === "restore_revision")
      return reject("MIXED_INSTRUCTION", "Undo, redo and revision restore must be sent on their own.");
    if (r.kind === "clarify") return { ...r, question: `Part ${bad + 1} (“${parts[bad]}”) needs a choice: ${r.question}`, options: [], notes: ["No part of this instruction was applied. Send that part on its own to choose an option."] };
    if (r.kind === "reject") return { ...r, message: `Part ${bad + 1} (“${parts[bad]}”) was rejected: ${r.message} No part was applied.` };
  }
  const all = results as Extract<Interpretation, { kind: "ops" }>[];
  return {
    kind: "ops",
    ops: all.flatMap((r) => r.ops),
    notes: all.flatMap((r, i) => [`Part ${i + 1}: “${parts[i]}” → ${r.ops.map((o) => o.type).join(", ")}`, ...r.notes]),
    parts,
  };
}

export { srcToTl };
