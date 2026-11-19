import { Pause, Play, SkipBack, Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { useEditor, usePlayhead } from "./editor-context";
import { Timecode } from "./Timecode";

const SIZE: Record<string, string> = { sm: "text-sm", md: "text-lg", lg: "text-2xl", xl: "text-3xl" };

export function PreviewPlayer({ url }: { url: string }) {
  const { engine, captions, durationTl, project, placed } = useEditor();
  const ph = usePlayhead();
  const ref = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(false);

  useEffect(() => {
    engine.attach(ref.current);
    return () => engine.attach(null);
  }, [engine, url]);

  const active = captions.flatMap((c) =>
    c.spans.filter((s) => ph.tl >= s.tlStart && ph.tl < s.tlEnd).map((s) => ({ c, s })),
  );
  const seg = ph.segIndex;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="relative flex min-h-0 flex-1 items-center justify-center bg-background p-3">
        <div className="relative max-h-full w-full max-w-full" style={{ aspectRatio: `${project.media.width || 16} / ${project.media.height || 9}`, maxHeight: "100%" }}>
          <video ref={ref} src={url} playsInline preload="auto" className="h-full w-full rounded-md bg-background object-contain ring-1 ring-border" onClick={() => engine.toggle()} />
          {active.map(({ c }) => (
            <div key={c.caption.id} className={cn("pointer-events-none absolute inset-x-0 flex justify-center px-6", c.caption.position === "top" ? "top-5" : "bottom-6")}>
              <span className={cn("rounded-md bg-background/80 px-3 py-1.5 font-semibold text-foreground shadow-lg ring-1 ring-border backdrop-blur", SIZE[c.caption.size])}>
                {c.caption.text}
              </span>
            </div>
          ))}
          {ph.audio !== "normal" && (
            <span className={cn("absolute right-3 top-3 rounded px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider", ph.audio === "bleep" ? "bg-bleep text-warning-foreground" : "bg-mute text-background")}>
              {ph.audio}
            </span>
          )}
        </div>
      </div>
      <div className="flex items-center gap-4 border-t border-border bg-panel px-4 py-2.5">
        <div className="flex items-center gap-1">
          <button aria-label="Go to start" className="grid h-8 w-8 place-items-center rounded-md hover:bg-secondary" onClick={() => engine.seekTl(0)}>
            <SkipBack className="h-4 w-4" />
          </button>
          <button aria-label={ph.playing ? "Pause" : "Play"} className="grid h-9 w-9 place-items-center rounded-full bg-primary text-primary-foreground hover:bg-primary/90" onClick={() => engine.toggle()}>
            {ph.playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
          </button>
          <button
            aria-label={muted ? "Unmute preview" : "Mute preview"}
            className="grid h-8 w-8 place-items-center rounded-md hover:bg-secondary"
            onClick={() => {
              engine.userMuted = !muted;
              setMuted(!muted);
            }}
          >
            {muted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
          </button>
        </div>
        <Timecode label="Timeline (edited)" ms={ph.tl} total={durationTl} tone="primary" />
        <Timecode label="Source (original)" ms={ph.src} total={project.media.durationMs} tone="teal" />
        <div className="ml-auto flex flex-col items-end leading-none">
          <span className="text-[9.5px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Segment · speed</span>
          <span className="tc mt-1 text-[13px]">
            #{seg + 1} of {placed.length} · {placed[seg]?.speed ?? 1}x
          </span>
        </div>
      </div>
    </div>
  );
}
