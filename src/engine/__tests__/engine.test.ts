import { describe, expect, it } from "vitest";
import lectureJson from "@/data/lecture-pricing.json";
import interviewJson from "@/data/interview-founder.json";
import { chooseOption, createSession, currentState, runInstruction, type Session } from "../executor";
import { importTranscript, isFiller, searchPhrase, transcriptWarnings } from "../transcript";
import { applyOp } from "../operations";
import { initialState, layout, resolveCaptions, srcToTl, timelineDuration, timelineHash, tlToSrc, placeWords } from "../timeline";
import { parseTime } from "../time";
import { buildRenderPlan, ffmpegCommand } from "../render";
import type { Transcript } from "../types";

const lecture = importTranscript(lectureJson);
const interview = importTranscript(interviewJson);
const L = () => createSession(lecture, lecture.durationMs);
const I = () => createSession(interview, interview.durationMs);
const last = (s: Session) => s.log[s.log.length - 1];
const run = (s: Session, text: string, ui = {}) => runInstruction(s, text, ui, 1);
const byId = (t: Transcript) => new Map(t.words.map((w) => [w.id, w]));
const ids = (t: Transcript, from: number, to: number) => t.words.filter((w) => w.start < to && w.end > from && Math.min(w.end, to) - Math.max(w.start, from) >= (w.end - w.start) / 2).map((w) => w.id);
const caps = (s: Session, t = lecture) => resolveCaptions(currentState(s), byId(t));
const visible = (s: Session, t = lecture) => placeWords(layout(currentState(s).segments), t.words);

describe("time parsing & mapping", () => {
  it("parses timecodes", () => {
    expect(parseTime("1:20")).toBe(80000);
    expect(parseTime("00:30.5")).toBe(30500);
    expect(parseTime("90s")).toBe(90000);
    expect(parseTime("1.5 minutes")).toBe(90000);
  });
  it("maps source to timeline after a cut", () => {
    const s = initialState(100000);
    const r = applyOp(s, { type: "cut", ranges: [{ start: 0, end: 30000 }] }, new Map());
    if (!r.ok) throw new Error();
    const p = layout(r.state.segments);
    expect(srcToTl(p, 42000)).toBe(12000);
    expect(srcToTl(p, 10000)).toBeNull();
    expect(tlToSrc(p, 12000)?.src).toBe(42000);
  });
  it("maps through speed changes", () => {
    let st = initialState(100000);
    const r = applyOp(st, { type: "speed", range: { start: 50000, end: 100000 }, speed: 2 }, new Map());
    if (!r.ok) throw new Error();
    st = r.state;
    expect(timelineDuration(st.segments)).toBe(75000);
    expect(srcToTl(layout(st.segments), 60000)).toBe(55000);
  });
  it("cut boundaries are exclusive of kept neighbours", () => {
    const r = applyOp(initialState(10000), { type: "cut", ranges: [{ start: 2000, end: 4000 }] }, new Map());
    if (!r.ok) throw new Error();
    expect(r.state.segments).toEqual([{ srcStart: 0, srcEnd: 2000, speed: 1 }, { srcStart: 4000, srcEnd: 10000, speed: 1 }]);
  });
});

describe("worked example (8 steps)", () => {
  it("runs end to end", () => {
    let s = L();
    // 1. load lecture
    expect(lecture.words.length).toBeGreaterThan(100);
    // 2. caption anchored to words at 42–44s
    s = run(s, 'Add caption "Welcome!" to words at 42-44');
    expect(last(s).status).toBe("applied");
    const anchored = currentState(s).captions[0].wordIds;
    expect(anchored).toEqual(ids(lecture, 42000, 44000));
    expect(caps(s)[0].spans[0].tlStart).toBe(42000);
    // 3. cut first 30 seconds
    s = run(s, "Cut the first 30 seconds");
    expect(last(s).status).toBe("applied");
    const cutEnd = currentState(s).segments[0].srcStart;
    expect(cutEnd).toBeLessThanOrEqual(30000); // snapped to a word edge
    // 4. caption follows its words
    const c = caps(s)[0];
    expect(c.caption.wordIds).toEqual(anchored);
    expect(c.spans[0].tlStart).toBeCloseTo(42000 - cutEnd, 3);
    // 5. search pricing -> all matches shown before cutting
    const before = timelineHash(currentState(s));
    s = run(s, "Remove the pricing section");
    expect(last(s).status).toBe("needs_clarification");
    expect(last(s).options!.length).toBe(3); // 2 visible matches + "all"
    expect(timelineHash(currentState(s))).toBe(before);
    // 6. 2x speed on a source range
    s = run(s, "Make the section after 1:20 play at 2x speed");
    expect(last(s).status).toBe("applied");
    expect(currentState(s).segments.some((g) => g.speed === 2 && g.srcStart === 80000)).toBe(true);
    const afterSpeed = timelineHash(currentState(s));
    // 7. restore the introduction, preserving other edits
    s = run(s, "Restore the earlier introduction");
    expect(last(s).status).toBe("applied");
    const st7 = currentState(s);
    expect(st7.segments[0].srcStart).toBe(0);
    expect(st7.segments.some((g) => g.speed === 2)).toBe(true);
    expect(caps(s)[0].spans[0].tlStart).toBe(42000);
    // 8. undo the restore
    s = run(s, "undo");
    expect(last(s).status).toBe("applied");
    expect(timelineHash(currentState(s))).toBe(afterSpeed);
    expect(caps(s)[0].spans[0].tlStart).toBeCloseTo(42000 - cutEnd, 3);
  });
});

describe("cuts", () => {
  it("cut section from 00:30 to 00:45 with word-safe snapping", () => {
    const s = run(L(), "Cut the section from 00:30 to 00:45");
    expect(last(s).status).toBe("applied");
    expect(last(s).notes.join(" ")).toMatch(/original source time/);
  });
  it("duplicate cut is rejected as NO_CHANGE and creates no revision", () => {
    let s = run(L(), "cut from 50 to 52");
    const revs = s.history.revisions.length;
    s = run(s, "cut from 50 to 52");
    expect(last(s).status).toBe("rejected");
    expect(last(s).error?.code).toBe("NO_CHANGE");
    expect(s.history.revisions.length).toBe(revs);
  });
  it("whole-video cut is rejected", () => {
    const s = run(L(), "cut the whole video");
    expect(last(s).error?.code).toBe("WHOLE_VIDEO_CUT");
  });
  it("range beyond duration is rejected", () => {
    const s = run(L(), "cut from 2:00 to 2:10");
    expect(last(s).error?.code).toBe("RANGE_OUT_OF_BOUNDS");
  });
  it("range ending past duration asks to clamp", () => {
    const s = run(L(), "cut from 1:30 to 2:30");
    expect(last(s).status).toBe("needs_clarification");
    expect(currentState(s).segments.length).toBe(1);
  });
  it("inverted range is rejected", () => {
    expect(last(run(L(), "cut from 0:40 to 0:20")).error?.code).toBe("INVALID_RANGE");
  });
  it("remove everything before the introduction cuts to its sentence start", () => {
    const s = run(L(), "Remove everything before the introduction");
    expect(last(s).status).toBe("applied");
    const um = lecture.words.find((w) => w.text === "Um,")!;
    expect(currentState(s).segments[0].srcStart).toBe(um.start);
  });
  it("overlapping cuts merge without double counting", () => {
    let s = run(L(), "cut from 50 to 60");
    s = run(s, "cut from 55 to 65");
    expect(last(s).status).toBe("applied");
    expect(currentState(s).segments.length).toBe(2);
  });
  it("a cut boundary inside a word never clips it", () => {
    const s = run(L(), "cut the first 30 seconds");
    const v = visible(s);
    for (const w of lecture.words) expect([0, 1].some((f) => Math.abs((v.get(w.id)!.keptFraction) - f) < 1e-9)).toBe(true);
  });
  it("cuts by current timeline time are mapped to source", () => {
    let s = run(L(), "cut the first 30 seconds");
    const offset = currentState(s).segments[0].srcStart;
    s = run(s, "cut from 0:20 to 0:25 in the current timeline");
    expect(last(s).status).toBe("applied");
    expect(last(s).notes.join(" ")).toMatch(/current timeline time/);
    expect(currentState(s).segments[1].srcStart).toBeGreaterThan(offset + 20000);
  });
  it("multiple mentions of a phrase ask which one; choosing applies once", () => {
    let s = run(L(), "Remove the pricing section");
    expect(last(s).options!.length).toBe(4);
    const q = last(s).id;
    s = chooseOption(s, q, 1, 1);
    expect(last(s).status).toBe("applied");
    s = chooseOption(s, q, 1, 1);
    expect(last(s).error?.code).toBe("ALREADY_RESOLVED");
  });
  it("stale clarification is refused", () => {
    let s = run(L(), "Remove the pricing section");
    const q = last(s).id;
    s = run(s, "cut from 95 to 99");
    s = chooseOption(s, q, 0, 1);
    expect(last(s).error?.code).toBe("STALE_CLARIFICATION");
  });
  it("single mention is applied directly", () => {
    const s = run(L(), "remove the word confidential");
    expect(last(s).status).toBe("applied");
  });
  it("semantic search offers related terms when no exact match", () => {
    const s = run(L(), "cut the fees section");
    expect(last(s).status).toBe("needs_clarification");
    expect(last(s).question).toMatch(/related/);
  });
  it("unknown phrase is rejected", () => {
    expect(last(run(L(), "remove the word zebra")).error?.code).toBe("NOT_FOUND");
  });
  it("unsupported instruction is rejected with a reason", () => {
    expect(last(run(L(), "make it cinematic please")).error?.code).toBe("UNSUPPORTED_INSTRUCTION");
  });
});

describe("speed & reorder", () => {
  it("speed outside limits is rejected", () => {
    expect(last(run(L(), "play from 10 to 20 at 9x speed")).error?.code).toBe("INVALID_SPEED");
  });
  it("captions on sped-up sections keep a ~0.7s minimum", () => {
    let s = run(L(), 'Add caption "Hi" to the word idea');
    s = runInstruction(s, "play from 0:40 to 1:00 at 4x speed", {}, 1);
    const sp = caps(s).find((c) => c.caption.text === "Hi")!.spans[0];
    expect(sp.tlEnd - sp.tlStart).toBeGreaterThanOrEqual(699);
  });
  it("reorders a selected section to the start; caption follows", () => {
    let s = run(L(), 'Add caption "Welcome!" to words at 42-44');
    const sel = ids(lecture, 42000, 45000);
    s = run(s, "move the selection to the start", { selection: sel });
    expect(last(s).status).toBe("applied");
    expect(currentState(s).segments[0].srcStart).toBe(42000);
    expect(caps(s)[0].spans[0].tlStart).toBe(0);
  });
  it("moving back to the original place restores the original hash", () => {
    const orig = timelineHash(currentState(L()));
    let s = run(L(), "move 0:41.5 to 0:45.5 to the start");
    s = run(s, "move 0:41.5 to 0:45.5 before 0:45.5");
    expect(last(s).status).toBe("applied");
    expect(timelineHash(currentState(s))).toBe(orig);
  });
});

describe("audio", () => {
  it("mutes the word confidential", () => {
    const s = run(L(), "Mute the word confidential");
    expect(last(s).status).toBe("applied");
    expect(currentState(s).effects[0].kind).toBe("mute");
  });
  it("bleeps selected words and maps them to the timeline", () => {
    const w = lecture.words.find((x) => x.text.startsWith("confidential"))!;
    let s = run(L(), "bleep the selected words", { selection: [w.id] });
    s = run(s, "cut the first 30 seconds");
    const plan = buildRenderPlan(currentState(s), lecture);
    expect(plan.audio[0].kind).toBe("bleep");
    expect(plan.audio[0].tlStart).toBeCloseTo(w.start - currentState(s).segments[0].srcStart, 3);
  });
  it("repeated mute is safe", () => {
    let s = run(L(), "mute the word confidential");
    s = run(s, "mute the word confidential");
    expect(last(s).error?.code).toBe("NO_CHANGE");
    expect(currentState(s).effects.length).toBe(1);
  });
  it("bleep without selection asks", () => {
    expect(last(run(L(), "bleep the selected words")).status).toBe("needs_clarification");
  });
});

describe("fillers & silence", () => {
  it("removes um/uh but keeps content 'like'", () => {
    const s = run(L(), "remove filler words");
    expect(last(s).status).toBe("applied");
    const v = visible(s);
    for (const w of lecture.words) {
      const n = w.text.toLowerCase().replace(/[^a-z]/g, "");
      if (n === "um" || n === "uh") expect(v.get(w.id)!.visible).toBe(false);
      if (n === "like") expect(v.get(w.id)!.visible).toBe(true);
    }
  });
  it("'like' set off by commas is a filler", () => {
    const t = importTranscript({ words: [{ text: "it", start: 0, end: 0.2 }, { text: "was,", start: 0.3, end: 0.5 }, { text: "like,", start: 0.6, end: 0.8 }, { text: "great.", start: 0.9, end: 1.2 }] });
    expect(isFiller(t.words, 2)).toBe(true);
  });
  it("removes silences longer than 2s but keeps deliberate pauses", () => {
    const s = run(L(), "remove silences longer than 2 seconds");
    expect(last(s).status).toBe("applied");
    expect(last(s).notes.join(" ")).toMatch(/deliberate pause/);
    const st = currentState(s);
    expect(timelineDuration(st.segments)).toBeLessThan(lecture.durationMs - 9000);
  });
  it("silence removal without threshold asks", () => {
    expect(last(run(L(), "remove the silences")).status).toBe("needs_clarification");
  });
});

describe("history", () => {
  it("undo reverses exactly one instruction and redo reapplies it", () => {
    let s = run(L(), "cut the first 10 seconds");
    const h1 = timelineHash(currentState(s));
    s = run(s, "mute the word confidential");
    s = run(s, "undo");
    expect(timelineHash(currentState(s))).toBe(h1);
    s = run(s, "redo");
    expect(currentState(s).effects.length).toBe(1);
  });
  it("new edit after undo clears redo", () => {
    let s = run(L(), "cut the first 10 seconds");
    s = run(s, "undo");
    s = run(s, "mute the word confidential");
    s = run(s, "redo");
    expect(last(s).error?.code).toBe("NOTHING_TO_REDO");
  });
  it("undo with nothing to undo is rejected", () => {
    expect(last(run(L(), "undo")).error?.code).toBe("NOTHING_TO_UNDO");
  });
  it("restore revision creates a new revision and is undoable", () => {
    let s = run(L(), "cut the first 10 seconds");
    s = run(s, "mute the word confidential");
    const h2 = timelineHash(currentState(s));
    s = run(s, "restore revision 0");
    expect(currentState(s).segments).toEqual([{ srcStart: 0, srcEnd: lecture.durationMs, speed: 1 }]);
    expect(s.history.revisions.length).toBe(4);
    s = run(s, "undo");
    expect(timelineHash(currentState(s))).toBe(h2);
  });
  it("captions return when their cut words are restored", () => {
    let s = run(L(), 'Add caption "Welcome!" to words at 42-44');
    s = run(s, "cut from 0:41 to 0:45");
    expect(caps(s)[0].status).toBe("removed");
    s = run(s, "restore 0:41 to 0:45");
    expect(caps(s)[0].status).toBe("visible");
    expect(caps(s)[0].spans[0].tlStart).toBe(42000);
  });
  it("partial caption removal keeps the caption on surviving words", () => {
    let s = run(L(), 'Add caption "Welcome!" to words at 42-44');
    s = run(s, "remove the word lesson");
    expect(caps(s)[0].status).toBe("partial");
  });
  it("restore of uncut content is rejected", () => {
    expect(last(run(L(), "restore the introduction")).error?.code).toBe("NO_CHANGE");
  });
});

describe("determinism", () => {
  it("same instructions produce identical hashes across three runs", () => {
    const script = ["cut the first 30 seconds", 'add caption "Welcome!" to words at 42-44', "make the section after 1:20 play at 2x speed", "remove filler words", "mute the word confidential"];
    const hashes = [0, 1, 2].map(() => {
      let s = L();
      for (const x of script) s = run(s, x);
      return timelineHash(currentState(s));
    });
    expect(new Set(hashes).size).toBe(1);
  });
  it("render plan and ffmpeg command are deterministic", () => {
    const s = run(run(L(), "cut the first 30 seconds"), "mute the word confidential");
    const a = ffmpegCommand(buildRenderPlan(currentState(s), lecture));
    const b = ffmpegCommand(buildRenderPlan(currentState(s), lecture));
    expect(a).toBe(b);
    expect(a).toContain("concat=n=");
  });
});

describe("multi-part & clarification", () => {
  it("multi-part instruction applies all parts as one revision", () => {
    const s = run(L(), "remove the first 10 seconds and mute the word confidential");
    expect(last(s).status).toBe("applied");
    expect(last(s).ops!.map((o) => o.type)).toEqual(["cut", "mute"]);
    expect(s.history.revisions.length).toBe(2);
  });
  it("multi-part with one invalid part applies nothing", () => {
    const s = run(L(), "remove the first 10 seconds and mute the word zebra");
    expect(last(s).status).toBe("rejected");
    expect(currentState(s).segments.length).toBe(1);
  });
  it("'make it bigger' without a selected caption asks which caption", () => {
    let s = run(L(), 'Add caption "Welcome!" to words at 42-44');
    s = run(s, "make it bigger");
    expect(last(s).status).toBe("needs_clarification");
    s = run(s, "make it bigger", { selectedCaptionId: currentState(s).captions[0].id });
    expect(currentState(s).captions[0].size).toBe("lg");
  });
});

describe("two speakers", () => {
  it("guest-only filler removal leaves host fillers", () => {
    const s = run(I(), "remove the guest's filler words");
    expect(last(s).status).toBe("applied");
    const v = visible(s, interview);
    const fillers = interview.words.filter((w) => /^(um|uh),?$/i.test(w.text));
    for (const w of fillers) expect(v.get(w.id)!.visible).toBe(w.speaker !== "S2");
  });
  it("ambiguous lead-ins ask host or guest", () => {
    const s = run(I(), "remove the lead-ins");
    expect(last(s).status).toBe("needs_clarification");
    expect(last(s).options!.map((o) => o.label)).toEqual(["Host only", "Guest only", "Both speakers"]);
  });
  it("host lead-ins target the host only", () => {
    const s = run(I(), "remove the host's lead-ins");
    expect(last(s).status).toBe("applied");
  });
  it("flags low-confidence words, overlap and label swaps", () => {
    const w = transcriptWarnings(interview);
    expect(w.filter((x) => x.kind === "low_confidence").length).toBe(2);
    const t = importTranscript({ words: [{ text: "a", start: 0, end: 0.5, speaker: "S1" }, { text: "b", start: 0.4, end: 0.8, speaker: "S2" }, { text: "c", start: 0.85, end: 1, speaker: "S1" }] });
    const k = transcriptWarnings(t).map((x) => x.kind);
    expect(k).toContain("overlap");
    expect(k).toContain("speaker_swap");
  });
  it("transcript import never invents confidence or speakers", () => {
    const t = importTranscript({ words: [{ text: "hi", start: 0, end: 0.3 }] });
    expect(t.words[0].confidence).toBeUndefined();
    expect(t.words[0].speaker).toBeUndefined();
    expect(searchPhrase(t.words, "hi").length).toBe(1);
  });
  it("imports Whisper verbose_json", () => {
    const t = importTranscript({ segments: [{ words: [{ word: " hello", start: 0, end: 0.4, probability: 0.9 }] }] });
    expect(t.words[0]).toMatchObject({ text: "hello", start: 0, end: 400, confidence: 0.9 });
  });
});
