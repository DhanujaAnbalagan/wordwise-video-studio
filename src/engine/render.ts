import { formatTc } from "./time";
import { layout, placeEffects, resolveCaptions, timelineDuration, timelineHash } from "./timeline";
import type { TimelineState, Transcript } from "./types";

export const ENGINE_VERSION = "videoflow-engine 1.0.0";
export const INTERPRETER_VERSION = "rule-based-interpreter 1.0.0";

export interface RenderPlan {
  version: 1;
  timelineHash: string;
  expectedDurationMs: number;
  segments: { index: number; srcStart: number; srcEnd: number; speed: number; tlStart: number; tlEnd: number }[];
  audio: { kind: "mute" | "bleep"; tlStart: number; tlEnd: number; wordId: string }[];
  captions: { id: string; text: string; tlStart: number; tlEnd: number; size: string; position: string; status: string }[];
  warnings: string[];
}

const r3 = (n: number) => Math.round(n * 1000) / 1000;

export function buildRenderPlan(state: TimelineState, transcript: Transcript): RenderPlan {
  const placed = layout(state.segments);
  const byId = new Map(transcript.words.map((w) => [w.id, w]));
  const caps = resolveCaptions(state, byId);
  const warnings: string[] = [];
  for (const c of caps) {
    if (c.status === "removed") warnings.push(`Caption “${c.caption.text}” is not rendered: all of its anchored words are cut.`);
    if (c.status === "partial") warnings.push(`Caption “${c.caption.text}”: ${c.totalWords - c.keptWords} of ${c.totalWords} anchored words are cut; shown over the remaining words.`);
    for (const s of c.spans) if (!s.minReached) warnings.push(`Caption “${c.caption.text}” at ${formatTc(s.tlStart)} could not reach the 0.7s minimum display time.`);
  }
  return {
    version: 1,
    timelineHash: timelineHash(state),
    expectedDurationMs: r3(timelineDuration(state.segments)),
    segments: placed.map((p) => ({ index: p.index, srcStart: p.srcStart, srcEnd: p.srcEnd, speed: p.speed, tlStart: r3(p.tlStart), tlEnd: r3(p.tlEnd) })),
    audio: placeEffects(state).map((e) => ({ kind: e.effect.kind, tlStart: r3(e.tlStart), tlEnd: r3(e.tlEnd), wordId: e.effect.wordId })),
    captions: caps.flatMap((c) =>
      c.spans.map((s) => ({ id: c.caption.id, text: c.caption.text, tlStart: r3(s.tlStart), tlEnd: r3(s.tlEnd), size: c.caption.size, position: c.caption.position, status: c.status })),
    ),
    warnings,
  };
}

function atempoChain(speed: number): string {
  const parts: string[] = [];
  let s = speed;
  while (s > 2) { parts.push("atempo=2.0"); s /= 2; }
  while (s < 0.5) { parts.push("atempo=0.5"); s /= 0.5; }
  parts.push(`atempo=${s.toFixed(6)}`);
  return parts.join(",");
}

const sec = (ms: number) => (ms / 1000).toFixed(3);
const esc = (t: string) => t.replace(/\\/g, "\\\\").replace(/'/g, "\u2019").replace(/:/g, "\\:").replace(/%/g, "\\%");

/** Equivalent FFmpeg command for the render plan (for offline/server rendering). */
export function ffmpegCommand(plan: RenderPlan, input = "input.mp4", output = "output.mp4"): string {
  const f: string[] = [];
  plan.segments.forEach((s, i) => {
    f.push(`[0:v]trim=start=${sec(s.srcStart)}:end=${sec(s.srcEnd)},setpts=(PTS-STARTPTS)/${s.speed}[v${i}]`);
    f.push(`[0:a]atrim=start=${sec(s.srcStart)}:end=${sec(s.srcEnd)},asetpts=PTS-STARTPTS,${atempoChain(s.speed)}[a${i}]`);
  });
  const n = plan.segments.length;
  f.push(`${plan.segments.map((_, i) => `[v${i}][a${i}]`).join("")}concat=n=${n}:v=1:a=1[vc][ac]`);
  const muteExpr = plan.audio.map((a) => `between(t,${sec(a.tlStart)},${sec(a.tlEnd)})`).join("+");
  f.push(muteExpr ? `[ac]volume=0:enable='${muteExpr}'[am]` : `[ac]anull[am]`);
  const bleeps = plan.audio.filter((a) => a.kind === "bleep");
  if (bleeps.length) {
    const be = bleeps.map((a) => `between(t,${sec(a.tlStart)},${sec(a.tlEnd)})`).join("+");
    f.push(`sine=frequency=1000:sample_rate=44100:duration=${sec(plan.expectedDurationMs)},volume=0.25,volume=0:enable='not(${be})'[beep]`);
    f.push(`[am][beep]amix=inputs=2:normalize=0:duration=first[aout]`);
  } else f.push(`[am]anull[aout]`);
  const sizes: Record<string, number> = { sm: 22, md: 30, lg: 40, xl: 52 };
  const draw = plan.captions.map(
    (c) => `drawtext=text='${esc(c.text)}':fontsize=${sizes[c.size] ?? 30}:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=12:x=(w-text_w)/2:y=${c.position === "top" ? "40" : "h-text_h-40"}:enable='between(t,${sec(c.tlStart)},${sec(c.tlEnd)})'`,
  );
  f.push(draw.length ? `[vc]${draw.join(",")}[vout]` : `[vc]null[vout]`);
  return `ffmpeg -y -i ${input} -filter_complex "${f.join(";\n")}" -map "[vout]" -map "[aout]" -c:v libx264 -crf 20 -c:a aac ${output}`;
}

export interface Manifest {
  project: string;
  timelineHash: string;
  revisionId: string;
  models: { name: string; version: string; role: string }[];
  settings: Record<string, unknown>;
  seed: string;
  timings: Record<string, number | string>;
  cost: string;
  warnings: string[];
  licence: string;
  source: { fileName: string; sha256: string; durationMs: number };
}

export function buildManifest(args: {
  projectName: string;
  revisionId: string;
  plan: RenderPlan;
  transcript: Transcript;
  source: { fileName: string; sha256: string; durationMs: number };
  isDemo: boolean;
  timings?: Record<string, number | string>;
  extraWarnings?: string[];
}): Manifest {
  return {
    project: args.projectName,
    timelineHash: args.plan.timelineHash,
    revisionId: args.revisionId,
    models: [
      { name: "Timeline engine", version: ENGINE_VERSION, role: "Deterministic edit application and source↔timeline mapping" },
      { name: "Instruction interpreter", version: INTERPRETER_VERSION, role: "Text → structured operations (no language model used)" },
      { name: "Transcript provider", version: [args.transcript.provider, args.transcript.model].filter(Boolean).join(" / "), role: "Word timestamps" },
      { name: "Browser renderer", version: "MediaRecorder (canvas + Web Audio), browser-native", role: "Real-time export" },
    ],
    settings: { minCaptionMs: 700, wordKeepFraction: 0.5, timePolicy: "Instruction timestamps = original source time unless 'current timeline' is stated" },
    seed: "Not applicable (no stochastic components)",
    timings: args.timings ?? {},
    cost: "0 — no paid speech-to-text or language-model APIs are called",
    warnings: [...args.plan.warnings, ...(args.extraWarnings ?? [])],
    licence: args.isDemo
      ? "Synthetic demo media created for this project with espeak-ng speech synthesis and FFmpeg; no real person's voice or likeness."
      : "User-supplied media. The uploader is responsible for holding the rights to edit and distribute it.",
    source: args.source,
  };
}
