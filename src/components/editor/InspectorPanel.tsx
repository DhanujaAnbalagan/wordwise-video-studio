import { AlertTriangle, Captions, Film } from "lucide-react";
import { formatTc } from "@/engine/time";
import { formatBytes } from "@/lib/media";
import { cn } from "@/lib/utils";
import { useEditor } from "./editor-context";

export function InspectorPanel() {
  const { captions, warnings, project, transcript, selectedCaptionId, setSelectedCaptionId, engine, setSelection } = useEditor();
  const m = project.media;
  return (
    <div className="scrollbar-thin h-full space-y-5 overflow-y-auto p-3">
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          <Captions className="h-3.5 w-3.5" /> Captions ({captions.length})
        </h3>
        {captions.length === 0 && <p className="text-xs text-muted-foreground">No captions. Select words and choose Caption, or type an instruction.</p>}
        <div className="space-y-1.5">
          {captions.map((c) => (
            <button
              key={c.caption.id}
              onClick={() => {
                setSelectedCaptionId(c.caption.id === selectedCaptionId ? null : c.caption.id);
                if (c.spans[0]) engine.seekTl(c.spans[0].tlStart);
              }}
              className={cn("w-full rounded-lg border p-2.5 text-left", c.caption.id === selectedCaptionId ? "border-primary bg-primary/10" : "border-border bg-panel hover:border-primary/40")}
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">“{c.caption.text}”</span>
                <span className={cn("text-[10px] font-semibold uppercase", c.status === "visible" ? "text-teal" : c.status === "partial" ? "text-warning" : "text-destructive")}>{c.status}</span>
              </div>
              <div className="tc mt-1 space-y-0.5 text-[10.5px] text-muted-foreground">
                <div>
                  anchors: {c.keptWords}/{c.totalWords} words · source {formatTc(c.anchor.start)}–{formatTc(c.anchor.end)}
                </div>
                <div>
                  now:{" "}
                  {c.spans.length ? c.spans.map((s) => `${formatTc(s.tlStart)}–${formatTc(s.tlEnd)}${s.extended ? " (≥0.7s)" : ""}`).join(", ") : "hidden — all anchored words are cut"}
                </div>
                <div>
                  {c.caption.id} · size {c.caption.size} · {c.caption.position}
                </div>
              </div>
            </button>
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          <AlertTriangle className="h-3.5 w-3.5" /> Transcript warnings ({warnings.length})
        </h3>
        {warnings.length === 0 && <p className="text-xs text-muted-foreground">No low-confidence words, overlaps or label swaps detected{transcript.words.some((w) => w.confidence != null) ? "" : " (this transcript has no confidence values)"}.</p>}
        <div className="space-y-1">
          {warnings.map((w, i) => (
            <button key={i} onClick={() => setSelection(w.wordIds)} className="flex w-full items-start gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-secondary">
              <span className={cn("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", w.kind === "low_confidence" ? "bg-warning" : "bg-destructive")} />
              {w.message}
            </button>
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          <Film className="h-3.5 w-3.5" /> Original media
        </h3>
        <dl className="tc grid grid-cols-[90px_1fr] gap-y-1 text-[11px]">
          <dt className="text-muted-foreground">File</dt>
          <dd className="truncate">{m.fileName}</dd>
          <dt className="text-muted-foreground">SHA-256</dt>
          <dd className="break-all">{m.sha256}</dd>
          <dt className="text-muted-foreground">Duration</dt>
          <dd>{formatTc(m.durationMs)}</dd>
          <dt className="text-muted-foreground">Frame</dt>
          <dd>
            {m.width}×{m.height}
          </dd>
          <dt className="text-muted-foreground">Size</dt>
          <dd>{formatBytes(m.sizeBytes)}</dd>
          <dt className="text-muted-foreground">Transcript</dt>
          <dd>
            {transcript.provider}
            {transcript.model ? ` · ${transcript.model}` : ""}
          </dd>
        </dl>
        {transcript.note && <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{transcript.note}</p>}
      </section>
    </div>
  );
}
