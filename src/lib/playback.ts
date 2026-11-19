/**
 * Plays the ORIGINAL video through the edited timeline: walks segments in playback order,
 * jumps over cuts, applies per-segment speed, and mutes / bleeps anchored words.
 */
import { layout, tlToSrc, type PlacedSegment } from "@/engine/timeline";
import type { AudioEffect, Segment } from "@/engine/types";

export interface PlayheadSnapshot {
  tl: number;
  src: number;
  playing: boolean;
  segIndex: number;
  audio: "normal" | "mute" | "bleep";
}

export class PlaybackEngine {
  private video: HTMLVideoElement | null = null;
  private placed: PlacedSegment[] = [];
  private effects: AudioEffect[] = [];
  private raf = 0;
  private listeners = new Set<() => void>();
  private ctx: AudioContext | null = null;
  private beepGain: GainNode | null = null;
  snapshot: PlayheadSnapshot = { tl: 0, src: 0, playing: false, segIndex: 0, audio: "normal" };
  userMuted = false;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getSnapshot = () => this.snapshot;

  private emit(patch: Partial<PlayheadSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((l) => l());
  }

  get duration() {
    return this.placed.length ? this.placed[this.placed.length - 1].tlEnd : 0;
  }

  attach(video: HTMLVideoElement | null) {
    this.video = video;
    if (video) {
      video.onended = () => this.advance();
      this.seekTl(this.snapshot.tl);
    }
  }

  setTimeline(segments: Segment[], effects: AudioEffect[]) {
    const prevSrc = this.snapshot.src;
    this.placed = layout(segments);
    this.effects = effects;
    const hit = this.placed.find((p) => p.srcStart <= prevSrc && prevSrc < p.srcEnd);
    if (hit) this.seekSrc(prevSrc);
    else this.seekTl(Math.min(this.snapshot.tl, this.duration));
  }

  private ensureAudio() {
    if (this.ctx) return;
    try {
      this.ctx = new AudioContext();
      const osc = this.ctx.createOscillator();
      osc.frequency.value = 1000;
      this.beepGain = this.ctx.createGain();
      this.beepGain.gain.value = 0;
      osc.connect(this.beepGain).connect(this.ctx.destination);
      osc.start();
    } catch {
      this.ctx = null;
    }
  }

  async play() {
    const v = this.video;
    if (!v || !this.placed.length) return;
    this.ensureAudio();
    void this.ctx?.resume();
    if (this.snapshot.tl >= this.duration - 20) this.seekTl(0);
    const seg = this.placed[this.snapshot.segIndex];
    if (seg) v.playbackRate = seg.speed;
    try {
      await v.play();
    } catch {
      return;
    }
    this.emit({ playing: true });
    cancelAnimationFrame(this.raf);
    this.raf = requestAnimationFrame(this.tick);
  }

  pause() {
    this.video?.pause();
    cancelAnimationFrame(this.raf);
    if (this.beepGain) this.beepGain.gain.value = 0;
    this.emit({ playing: false });
  }

  toggle() {
    if (this.snapshot.playing) this.pause();
    else void this.play();
  }

  seekTl(x: number) {
    const r = tlToSrc(this.placed, x);
    if (!r) return;
    this.jump(r.segIndex, r.src);
  }

  seekSrc(src: number) {
    let idx = this.placed.findIndex((p) => p.srcStart <= src && src < p.srcEnd);
    if (idx < 0) {
      // Source moment is cut: go to the next kept source moment.
      const after = this.placed.filter((p) => p.srcStart >= src).sort((a, b) => a.srcStart - b.srcStart)[0];
      if (!after) return;
      idx = after.index;
      src = after.srcStart;
    }
    this.jump(idx, src);
  }

  private jump(segIndex: number, src: number) {
    const seg = this.placed[segIndex];
    if (!seg) return;
    if (this.video) {
      this.video.currentTime = src / 1000;
      this.video.playbackRate = seg.speed;
    }
    this.emit({ segIndex, src, tl: seg.tlStart + (src - seg.srcStart) / seg.speed, audio: this.audioAt(src) });
    this.applyAudio(this.audioAt(src));
  }

  private audioAt(src: number): PlayheadSnapshot["audio"] {
    let mode: PlayheadSnapshot["audio"] = "normal";
    for (const e of this.effects) {
      if (e.srcStart <= src && src < e.srcEnd) {
        if (e.kind === "bleep") return "bleep";
        mode = "mute";
      }
    }
    return mode;
  }

  private applyAudio(mode: PlayheadSnapshot["audio"]) {
    if (this.video) this.video.muted = this.userMuted || mode !== "normal";
    if (this.beepGain) this.beepGain.gain.value = mode === "bleep" && this.snapshot.playing && !this.userMuted ? 0.12 : 0;
  }

  private advance() {
    const next = this.snapshot.segIndex + 1;
    if (next >= this.placed.length) {
      this.pause();
      const last = this.placed[this.placed.length - 1];
      this.emit({ tl: this.duration, src: last?.srcEnd ?? 0 });
      return;
    }
    this.jump(next, this.placed[next].srcStart);
  }

  private tick = () => {
    const v = this.video;
    if (!v) return;
    const src = v.currentTime * 1000;
    let seg = this.placed[this.snapshot.segIndex];
    if (seg && (src < seg.srcStart - 120 || src > seg.srcEnd + 250)) {
      const idx = this.placed.findIndex((p) => p.srcStart <= src && src < p.srcEnd);
      if (idx >= 0) seg = this.placed[idx];
      this.snapshot.segIndex = idx >= 0 ? idx : this.snapshot.segIndex;
    }
    if (seg && src >= seg.srcEnd - 10) {
      this.advance();
    } else if (seg) {
      const c = Math.max(seg.srcStart, src);
      const audio = this.audioAt(c);
      this.applyAudio(audio);
      this.emit({ src: c, tl: seg.tlStart + (c - seg.srcStart) / seg.speed, audio });
    }
    if (this.snapshot.playing) this.raf = requestAnimationFrame(this.tick);
  };

  dispose() {
    cancelAnimationFrame(this.raf);
    void this.ctx?.close();
    this.listeners.clear();
  }
}
