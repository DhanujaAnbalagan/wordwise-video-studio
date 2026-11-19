import { createContext, useContext, useSyncExternalStore } from "react";
import type { Session } from "@/engine/executor";
import type { CaptionDisplay, PlacedEffect, PlacedSegment, WordPlacement } from "@/engine/timeline";
import type { TranscriptWarning } from "@/engine/transcript";
import type { TimelineState, Transcript, Word } from "@/engine/types";
import type { PlaybackEngine, PlayheadSnapshot } from "@/lib/playback";
import type { ProjectRecord } from "@/lib/storage";

export interface EditorCtx {
  project: ProjectRecord;
  session: Session;
  transcript: Transcript;
  state: TimelineState;
  placed: PlacedSegment[];
  placements: Map<string, WordPlacement>;
  captions: CaptionDisplay[];
  effects: PlacedEffect[];
  wordsById: Map<string, Word>;
  warnings: TranscriptWarning[];
  durationTl: number;
  engine: PlaybackEngine;
  selection: string[];
  setSelection: (ids: string[]) => void;
  selectedCaptionId: string | null;
  setSelectedCaptionId: (id: string | null) => void;
  run: (text: string) => void;
  choose: (resultId: string, idx: number) => void;
  importTranscriptFile: (file: File) => Promise<void>;
}

export const EditorContext = createContext<EditorCtx | null>(null);

export function useEditor() {
  const c = useContext(EditorContext);
  if (!c) throw new Error("useEditor outside EditorContext");
  return c;
}

const SERVER: PlayheadSnapshot = { tl: 0, src: 0, playing: false, segIndex: 0, audio: "normal" };

export function usePlayhead(): PlayheadSnapshot {
  const { engine } = useEditor();
  return useSyncExternalStore(engine.subscribe, engine.getSnapshot, () => SERVER);
}
