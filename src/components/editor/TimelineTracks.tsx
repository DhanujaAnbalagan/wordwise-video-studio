import { Minus, Plus, Scissors } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatTc } from "@/engine/time";
import { presentIntervals } from "@/engine/timeline";
import { cn } from "@/lib/utils";
import { useEditor, usePlayhead } from "./editor-context";

const GUTTER = 76;
const STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120];

export function TimelineTracks() {
  const { placed, effects, captions, transcript, placements, durationTl, engine, state, selectedCaptionId, setSelectedCaptionId, selection } = useEditor();
  const ph = usePlayhead();
  const scroller = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const durS = Math.max(durationTl / 1000, 1);
  const pps = ((width - 24) / durS) * zoom;
  const total = durS * pps;
  const x = (ms: number) => (ms / 1000) * pps;
  const step = STEPS.find((s) => s * pps >= 70) ?? 300;
  const ticks = useMemo(() => {
    const out: number[] = [];
    for (let t = 0; t <= durS + 1e-6; t += step) out.push(t);
    return out;
  }, [durS, step]);

  useEffect(() => {
    const el = scroller.current;
    if (!el || !ph.playing) return;
    const px = x(ph.tl);
    if (px < el.scrollLeft || px > el.scrollLeft + el.clientWidth - 40) el.scrollLeft = Math.max(0, px - 80);
  });

  const srcDur = state.sourceDurationMs;
  const present = presentIntervals(state.segments);
  const selSet = new Set(selection);

  const seekFromEvent = (e: React.MouseEvent) => {
    const el = scroller.current!;
    const rect = el.getBoundingClientRect();
    const px = e.clientX - rect.left + el.scrollLeft;
    engine.seekTl(Math.max(0, Math.min(durationTl, (px / pps) * 1000)));
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-panel">
      <div className="flex items-center gap-3 border-b border-border px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Timeline</span>
        <span className="tc text-[11px] text-muted-foreground">
          {placed.length} segment{placed.length === 1 ? "" : "s"} · {formatTc(durationTl)} edited · {formatTc(srcDur)} source
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button aria-label="Zoom out" className="grid h-6 w-6 place-items-center rounded hover:bg-secondary" onClick={() => setZoom((z) => Math.max(1, z / 1.5))}>
            <Minus className="h-3.5 w-3.5" />
          </button>
          <span className="tc w-10 text-center text-[11px] text-muted-foreground">{zoom.toFixed(1)}x</span>
          <button aria-label="Zoom in" className="grid h-6 w-6 place-items-center rounded hover:bg-secondary" onClick={() => setZoom((z) => Math.min(20, z * 1.5))}>
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Source map: the ORIGINAL video, with what is kept, cut and in which playback order. */}
      <div className="flex items-center border-b border-border">
        <div style={{ width: GUTTER }} className="shrink-0 px-3 text-[10px] font-semibold uppercase tracking-wider text-teal">
          Source
        </div>
        <div className="relative mr-3 h-6 flex-1 overflow-hidden rounded-sm bg-destructive/10" title="Original source: coloured = kept, hatched = cut">
          <div className="absolute inset-0 opacity-40 [background-image:repeating-linear-gradient(-45deg,var(--destructive)_0_2px,transparent_2px_6px)]" />
          {present.map((p) => (
            <div key={p.start} className="absolute inset-y-0 bg-track" style={{ left: `${(p.start / srcDur) * 100}%`, width: `${((p.end - p.start) / srcDur) * 100}%` }} />
          ))}
          {placed.map((p) => (
            <div
              key={p.index}
              className={cn("tc absolute inset-y-0.5 flex items-center justify-center overflow-hidden rounded-[3px] text-[10px] font-semibold", p.speed !== 1 ? "bg-clip-fast/80 text-foreground" : "bg-clip/80 text-foreground")}
              style={{ left: `${(p.srcStart / srcDur) * 100}%`, width: `${((p.srcEnd - p.srcStart) / srcDur) * 100}%` }}
            >
              {p.index + 1}
            </div>
          ))}
          <div className="absolute inset-y-0 w-0.5 bg-teal" style={{ left: `${(ph.src / srcDur) * 100}%` }} />
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div style={{ width: GUTTER }} className="shrink-0 border-r border-border text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          <div className="h-6" />
          <div className="flex h-12 items-center px-3">V1 Video</div>
          <div className="flex h-10 items-center px-3">A1 Audio</div>
          <div className="flex h-8 items-center px-3">Captions</div>
          <div className="flex h-7 items-center px-3">Words</div>
        </div>
        <div ref={scroller} className="scrollbar-thin relative min-w-0 flex-1 overflow-x-auto overflow-y-hidden" onMouseDown={seekFromEvent}>
          <div className="relative" style={{ width: total + 24 }}>
            {/* Ruler (timeline time) */}
            <div className="relative h-6 border-b border-border">
              {ticks.map((t) => (
                <div key={t} className="absolute top-0 h-full border-l border-border/80 pl-1" style={{ left: t * pps }}>
                  <span className="tc text-[10px] text-muted-foreground">{formatTc(t * 1000, false)}</span>
                </div>
              ))}
            </div>
            {/* V1 */}
            <div className="relative h-12">
              {placed.map((p) => (
                <div
                  key={p.index}
                  className={cn("absolute inset-y-1 overflow-hidden rounded-md border px-1.5 py-1", p.speed !== 1 ? "border-teal/50 bg-clip-fast/50" : "border-primary/40 bg-clip/60")}
                  style={{ left: x(p.tlStart), width: Math.max(2, x(p.tlEnd) - x(p.tlStart) - 1) }}
                  title={`Segment ${p.index + 1}: source ${formatTc(p.srcStart)}–${formatTc(p.srcEnd)} at ${p.speed}x → timeline ${formatTc(p.tlStart)}–${formatTc(p.tlEnd)}`}
                >
                  <div className="tc truncate text-[10px] font-medium text-foreground">
                    #{p.index + 1} {p.speed !== 1 && <span className="rounded bg-teal px-1 text-teal-foreground">{p.speed}x</span>}
                  </div>
                  <div className="tc truncate text-[9.5px] text-foreground/70">
                    src {formatTc(p.srcStart, false)}–{formatTc(p.srcEnd, false)}
                  </div>
                </div>
              ))}
              {placed.slice(1).map((p) => (
                <div key={`cut${p.index}`} className="absolute top-0 z-10 flex h-full -translate-x-1/2 items-start" style={{ left: x(p.tlStart) }} title="Edit point">
                  <Scissors className="h-3 w-3 text-muted-foreground" />
                </div>
              ))}
            </div>
            {/* A1 */}
            <div className="relative h-10">
              {placed.map((p) => (
                <div
                  key={p.index}
                  className="absolute inset-y-1 overflow-hidden rounded-md border border-border bg-track [background-image:repeating-linear-gradient(90deg,color-mix(in_oklab,var(--teal)_35%,transparent)_0_1px,transparent_1px_4px)]"
                  style={{ left: x(p.tlStart), width: Math.max(2, x(p.tlEnd) - x(p.tlStart) - 1) }}
                />
              ))}
              {effects.map((e, i) => (
                <div
                  key={`${e.effect.id}-${i}`}
                  className={cn("absolute inset-y-1 rounded-sm", e.effect.kind === "bleep" ? "bg-bleep/80" : "hatch bg-background/60")}
                  style={{ left: x(e.tlStart), width: Math.max(3, x(e.tlEnd) - x(e.tlStart)) }}
                  title={`${e.effect.kind} · ${e.effect.wordId} · timeline ${formatTc(e.tlStart)}`}
                />
              ))}
            </div>
            {/* Captions */}
            <div className="relative h-8">
              {captions.flatMap((c) =>
                c.spans.map((s, i) => (
                  <button
                    key={`${c.caption.id}-${i}`}
                    onMouseDown={(ev) => {
                      ev.stopPropagation();
                      setSelectedCaptionId(c.caption.id === selectedCaptionId ? null : c.caption.id);
                      engine.seekTl(s.tlStart);
                    }}
                    className={cn(
                      "absolute inset-y-1 truncate rounded px-1.5 text-left text-[10.5px] font-medium",
                      c.caption.id === selectedCaptionId ? "bg-primary text-primary-foreground ring-2 ring-primary/50" : "bg-warning/80 text-warning-foreground",
                      c.status === "partial" && "opacity-80 [background-image:repeating-linear-gradient(-45deg,transparent_0_4px,color-mix(in_oklab,var(--background)_25%,transparent)_4px_6px)]",
                    )}
                    style={{ left: x(s.tlStart), width: Math.max(10, x(s.tlEnd) - x(s.tlStart)) }}
                    title={`${c.caption.text} — anchored to ${c.totalWords} word(s) at source ${formatTc(c.anchor.start)}–${formatTc(c.anchor.end)}${s.extended ? " · extended to 0.7s minimum" : ""}`}
                  >
                    {c.caption.text}
                  </button>
                )),
              )}
            </div>
            {/* Words */}
            <div className="relative h-7">
              {transcript.words.map((w) => {
                const p = placements.get(w.id);
                if (!p?.visible || p.tlStart == null || p.tlEnd == null) return null;
                const low = w.confidence != null && w.confidence < 0.6;
                return (
                  <div
                    key={w.id}
                    className={cn("absolute top-1.5 h-4 rounded-[2px]", selSet.has(w.id) ? "bg-primary" : low ? "bg-warning/80" : "bg-muted-foreground/40")}
                    style={{ left: x(p.tlStart), width: Math.max(1.5, x(p.tlEnd) - x(p.tlStart) - 0.5) }}
                    title={`${w.text} (${w.id})`}
                  />
                );
              })}
            </div>
            {/* Playhead */}
            <div className="pointer-events-none absolute top-0 bottom-0 z-20 w-px bg-primary" style={{ left: x(ph.tl) }}>
              <div className="absolute -left-[5px] -top-0.5 h-0 w-0 border-x-[5px] border-t-[7px] border-x-transparent border-t-primary" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
