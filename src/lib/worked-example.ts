import lectureJson from "@/data/lecture-pricing.json";
import { createSession, currentState, runInstruction, type Session } from "@/engine/executor";
import { formatTc } from "@/engine/time";
import { resolveCaptions, timelineHash } from "@/engine/timeline";
import { importTranscript } from "@/engine/transcript";

export interface StepEvidence {
  n: number;
  title: string;
  instruction: string | null;
  status: string;
  evidence: string[];
  pass: boolean;
}

export const WORKED_INSTRUCTIONS = [
  'Add caption "Welcome!" to words at 42-44',
  "Cut the first 30 seconds",
  "Remove the pricing section",
  "Make the section after 1:20 play at 2x speed",
  "Restore the earlier introduction",
  "Undo",
];

/** Executes the eight-step demonstration against the real engine and records evidence. */
export function runWorkedExample(): { steps: StepEvidence[]; session: Session } {
  const t = importTranscript(lectureJson);
  let s = createSession(t, t.durationMs, 0);
  const byId = new Map(t.words.map((w) => [w.id, w]));
  const cap = () => resolveCaptions(currentState(s), byId)[0];
  const last = () => s.log[s.log.length - 1];
  const steps: StepEvidence[] = [];

  steps.push({ n: 1, title: "Load the lecture video", instruction: null, status: "loaded", evidence: [`${t.words.length} words, source ${formatTc(t.durationMs)}`, `r0 hash ${timelineHash(currentState(s)).slice(0, 16)}`], pass: t.words.length > 0 });

  s = runInstruction(s, WORKED_INSTRUCTIONS[0], {}, 0);
  const c1 = cap();
  const words = c1.caption.wordIds.map((id) => byId.get(id)!);
  steps.push({
    n: 2,
    title: "Caption “Welcome!” anchored to words at 42–44 s",
    instruction: WORKED_INSTRUCTIONS[0],
    status: last().status,
    evidence: [`Anchors: ${words.map((w) => `${w.id} “${w.text}”`).join(", ")}`, `Shown at timeline ${formatTc(c1.spans[0].tlStart)}`],
    pass: last().status === "applied" && c1.spans[0].tlStart === 42000,
  });

  s = runInstruction(s, WORKED_INSTRUCTIONS[1], {}, 0);
  const cutEnd = currentState(s).segments[0].srcStart;
  steps.push({ n: 3, title: "Cut the first 30 seconds", instruction: WORKED_INSTRUCTIONS[1], status: last().status, evidence: [...last().notes.slice(0, 2), `Timeline now starts at source ${formatTc(cutEnd)}`], pass: last().status === "applied" });

  const c3 = cap();
  steps.push({
    n: 4,
    title: "Caption follows its original words",
    instruction: null,
    status: "verified",
    evidence: [`Same anchors (${c3.caption.wordIds.length} words)`, `Timeline ${formatTc(42000)} → ${formatTc(c3.spans[0].tlStart)} (= 42.000 − ${(cutEnd / 1000).toFixed(3)})`],
    pass: Math.abs(c3.spans[0].tlStart - (42000 - cutEnd)) < 0.5 && c3.caption.wordIds.join() === c1.caption.wordIds.join(),
  });

  const h4 = timelineHash(currentState(s));
  s = runInstruction(s, WORKED_INSTRUCTIONS[2], {}, 0);
  steps.push({
    n: 5,
    title: "Search “pricing” — show all matches before cutting",
    instruction: WORKED_INSTRUCTIONS[2],
    status: last().status,
    evidence: [last().question ?? "", ...(last().options ?? []).map((o) => `${o.label}: ${o.detail}`), `Timeline unchanged: ${timelineHash(currentState(s)) === h4}`],
    pass: last().status === "needs_clarification" && timelineHash(currentState(s)) === h4,
  });

  s = runInstruction(s, WORKED_INSTRUCTIONS[3], {}, 0);
  const sped = currentState(s).segments.find((g) => g.speed === 2);
  const h6 = timelineHash(currentState(s));
  steps.push({ n: 6, title: "2x speed on source range 1:20 → end", instruction: WORKED_INSTRUCTIONS[3], status: last().status, evidence: [last().notes[0] ?? "", sped ? `Segment source ${formatTc(sped.srcStart)}–${formatTc(sped.srcEnd)} at 2x` : "no 2x segment"], pass: last().status === "applied" && !!sped });

  s = runInstruction(s, WORKED_INSTRUCTIONS[4], {}, 0);
  const st7 = currentState(s);
  const c7 = cap();
  steps.push({
    n: 7,
    title: "Restore the introduction, keeping other edits",
    instruction: WORKED_INSTRUCTIONS[4],
    status: last().status,
    evidence: [last().notes[0] ?? "", `2x segment kept: ${st7.segments.some((g) => g.speed === 2)}`, `Caption back at ${formatTc(c7.spans[0].tlStart)}`],
    pass: last().status === "applied" && st7.segments[0].srcStart === 0 && st7.segments.some((g) => g.speed === 2) && c7.spans[0].tlStart === 42000,
  });

  s = runInstruction(s, WORKED_INSTRUCTIONS[5], {}, 0);
  const c8 = cap();
  steps.push({
    n: 8,
    title: "Undo the restore",
    instruction: WORKED_INSTRUCTIONS[5],
    status: last().status,
    evidence: [last().message, `Hash equals step 6: ${timelineHash(currentState(s)) === h6}`, `Caption at ${formatTc(c8.spans[0].tlStart)}`],
    pass: last().status === "applied" && timelineHash(currentState(s)) === h6,
  });
  return { steps, session: s };
}
