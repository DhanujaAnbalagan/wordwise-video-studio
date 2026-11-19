import lectureJson from "@/data/lecture-pricing.json";
import interviewJson from "@/data/interview-founder.json";
import { createSession } from "@/engine/executor";
import { importTranscript } from "@/engine/transcript";
import type { Transcript } from "@/engine/types";
import { checkFormat, probeVideo, sha256Hex } from "./media";
import { newId, putMedia, putProject, type ProjectRecord } from "./storage";

export const DEMOS = [
  {
    key: "lecture",
    name: "Pricing Strategy 101 (lecture)",
    url: "/demo/lecture-pricing.mp4",
    fileName: "lecture-pricing.mp4",
    transcript: lectureJson,
    blurb: "Single speaker · 1:40 · mentions “pricing” three times",
  },
  {
    key: "interview",
    name: "Founder Interview (two speakers)",
    url: "/demo/interview-founder.mp4",
    fileName: "interview-founder.mp4",
    transcript: interviewJson,
    blurb: "Host + guest · 0:47 · fillers, lead-ins, low-confidence words",
  },
] as const;

export async function createDemoProject(key: string): Promise<ProjectRecord> {
  const demo = DEMOS.find((d) => d.key === key)!;
  const res = await fetch(demo.url);
  if (!res.ok) throw new Error(`Could not load the demo video (${res.status}).`);
  const blob = await res.blob();
  const [sha256, meta] = await Promise.all([sha256Hex(blob), probeVideo(demo.url)]);
  const transcript = importTranscript(demo.transcript, meta.durationMs);
  const now = Date.now();
  const p: ProjectRecord = {
    id: newId(),
    name: demo.name,
    createdAt: now,
    updatedAt: now,
    status: "ready",
    isDemo: true,
    media: { fileName: demo.fileName, mime: "video/mp4", sizeBytes: blob.size, sha256, ...meta, demoUrl: demo.url },
    session: createSession(transcript, meta.durationMs, now),
  };
  await putProject(p);
  return p;
}

export function emptyTranscript(durationMs: number): Transcript {
  return { provider: "none", note: "No transcript imported yet.", durationMs, speakers: {}, words: [] };
}

export async function createUploadProject(file: File, name: string, transcriptFile?: File | null): Promise<ProjectRecord> {
  checkFormat(file);
  const url = URL.createObjectURL(file);
  try {
    const [sha256, meta] = await Promise.all([sha256Hex(file), probeVideo(url)]);
    let transcript = emptyTranscript(meta.durationMs);
    if (transcriptFile) transcript = importTranscript(JSON.parse(await transcriptFile.text()), meta.durationMs);
    const now = Date.now();
    const id = newId();
    await putMedia(id, file);
    const p: ProjectRecord = {
      id,
      name: name.trim() || file.name.replace(/\.[^.]+$/, ""),
      createdAt: now,
      updatedAt: now,
      status: transcript.words.length ? "ready" : "needs_transcript",
      isDemo: false,
      media: { fileName: file.name, mime: file.type || "video/mp4", sizeBytes: file.size, sha256, ...meta },
      session: createSession(transcript, meta.durationMs, now),
    };
    await putProject(p);
    return p;
  } finally {
    URL.revokeObjectURL(url);
  }
}
