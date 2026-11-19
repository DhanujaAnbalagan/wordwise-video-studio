/**
 * VideoFlow timeline model.
 *
 * TIME POLICY: every `Ms` value in this module is an integer number of milliseconds of
 * ORIGINAL SOURCE time unless the field name ends in `Tl` / starts with `tl`, which means
 * CURRENT EDITED TIMELINE time (a float, derived — never stored as identity).
 */
export type Ms = number;

export interface Interval {
  start: Ms;
  end: Ms;
}

export interface Word {
  id: string;
  index: number;
  text: string;
  start: Ms;
  end: Ms;
  speaker?: string;
  confidence?: number;
}

export interface Transcript {
  provider: string;
  model?: string;
  note?: string;
  durationMs: Ms;
  speakers: Record<string, { label: string }>;
  words: Word[];
}

/** One piece of the original source, played in list order at `speed`. */
export interface Segment {
  srcStart: Ms;
  srcEnd: Ms;
  speed: number;
}

export type CaptionSize = "sm" | "md" | "lg" | "xl";

export interface Caption {
  id: string;
  text: string;
  /** Stable anchors: captions are identified by the source words they belong to. */
  wordIds: string[];
  size: CaptionSize;
  position: "bottom" | "top";
}

export interface AudioEffect {
  id: string;
  kind: "mute" | "bleep";
  wordId: string;
  srcStart: Ms;
  srcEnd: Ms;
}

export interface TimelineState {
  sourceDurationMs: Ms;
  segments: Segment[];
  captions: Caption[];
  effects: AudioEffect[];
  nextId: number;
}

export type Op =
  | { type: "cut"; ranges: Interval[]; reason?: string }
  | { type: "restore"; ranges: Interval[]; reason?: string }
  | { type: "speed"; range: Interval; speed: number }
  | { type: "mute" | "bleep" | "clear_audio"; wordIds: string[] }
  | { type: "add_caption"; text: string; wordIds: string[] }
  | { type: "remove_caption"; captionId: string }
  | {
      type: "style_caption";
      captionId: string;
      sizeStep?: 1 | -1;
      position?: "top" | "bottom";
    }
  | { type: "move"; range: Interval; to: "start" | "end" | { beforeSrc: Ms } };

export type InstructionStatus = "applied" | "needs_clarification" | "rejected";

export interface EngineError {
  code: string;
  message: string;
  retryable: false;
  details: Record<string, unknown>;
}

export interface ClarificationOption {
  label: string;
  detail?: string;
  ops: Op[];
}

export interface StateSummary {
  durationTl: number;
  segments: number;
  visibleWords: number;
  captionsVisible: number;
  captionsHidden: number;
  effects: number;
  hash: string;
}

export interface InstructionResult {
  id: string;
  input: string;
  status: InstructionStatus;
  createdAt: number;
  latencyMs: number;
  message: string;
  notes: string[];
  ops?: Op[];
  revisionId?: string;
  before?: StateSummary;
  after?: StateSummary;
  question?: string;
  options?: ClarificationOption[];
  basedOnHash?: string;
  resolvedWith?: number;
  error?: EngineError;
}

export interface Revision {
  id: string;
  index: number;
  parentId: string | null;
  kind: "initial" | "edit" | "restore";
  label: string;
  ops: Op[];
  state: TimelineState;
  hash: string;
  createdAt: number;
}

export interface History {
  revisions: Revision[];
  /** Applied path; last element is the current head. */
  undo: string[];
  /** Reverted revisions that can be redone; last element is redone first. */
  redo: string[];
}
