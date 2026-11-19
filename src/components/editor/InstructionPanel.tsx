import { ChevronRight, CornerDownLeft, Info, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { formatTc } from "@/engine/time";
import type { InstructionResult } from "@/engine/types";
import { StatusBadge } from "@/components/vf/StatusBadge";
import { cn } from "@/lib/utils";
import { useEditor } from "./editor-context";

const EXAMPLES = [
  "Remove the first 10 seconds",
  "Cut the section from 00:30 to 00:45",
  "Remove everything before the introduction",
  "Make the section after 1:20 play at 2x speed",
  "Mute the word confidential",
  'Add caption "Welcome!" to words at 42-44',
  "Remove the pricing section",
  "Remove filler words",
  "Remove silences longer than 2 seconds",
  "Remove the lead-ins",
  "Restore the introduction",
  "Make it bigger",
  "Cut the whole video",
];

function ResultCard({ r, latest }: { r: InstructionResult; latest: boolean }) {
  const { choose } = useEditor();
  const [open, setOpen] = useState(false);
  return (
    <div className={cn("rounded-lg border bg-panel p-3", r.status === "applied" ? "border-teal/25" : r.status === "rejected" ? "border-destructive/25" : "border-warning/30")}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-[13px] font-medium leading-snug text-foreground">“{r.input}”</p>
        <StatusBadge status={r.status} className="shrink-0" />
      </div>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">{r.message}</p>
      {r.error && (
        <p className="tc mt-1 text-[11px] text-destructive">
          {r.error.code} · retryable: false
        </p>
      )}
      {r.notes.length > 0 && (
        <ul className="mt-2 space-y-1 border-l-2 border-border pl-2.5">
          {r.notes.map((n, i) => (
            <li key={i} className="text-[11.5px] leading-snug text-muted-foreground">
              {n}
            </li>
          ))}
        </ul>
      )}
      {r.status === "needs_clarification" && r.options && (
        <div className="mt-2.5 space-y-1.5">
          {r.options.map((o, i) => (
            <button
              key={i}
              disabled={r.resolvedWith != null || !latest}
              onClick={() => choose(r.id, i)}
              className={cn(
                "w-full rounded-md border px-2.5 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed",
                r.resolvedWith === i ? "border-teal bg-teal/10" : "border-border bg-background hover:border-warning/70 disabled:opacity-50",
              )}
            >
              <span className="font-semibold">{o.label}</span>
              {o.detail && <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">{o.detail}</span>}
            </button>
          ))}
          {r.options.length === 0 && <p className="text-[11.5px] text-warning">Select words in the transcript, then send the instruction again.</p>}
          {r.resolvedWith == null && !latest && <p className="text-[11px] text-muted-foreground">Superseded by a newer instruction — send it again to choose.</p>}
        </div>
      )}
      {r.status === "applied" && r.before && r.after && (
        <div className="tc mt-2.5 grid grid-cols-2 gap-x-3 gap-y-0.5 rounded-md bg-background/60 px-2.5 py-2 text-[10.5px]">
          <span className="text-muted-foreground">Duration</span>
          <span>
            {formatTc(r.before.durationTl)} → <span className="text-teal">{formatTc(r.after.durationTl)}</span>
          </span>
          <span className="text-muted-foreground">Segments / captions</span>
          <span>
            {r.before.segments}/{r.before.captionsVisible} → {r.after.segments}/{r.after.captionsVisible}
          </span>
          <span className="text-muted-foreground">Revision</span>
          <span className="text-primary">{r.revisionId}</span>
          <span className="text-muted-foreground">Timeline hash</span>
          <span className="truncate" title={r.after.hash}>
            {r.after.hash.slice(0, 16)}…
          </span>
        </div>
      )}
      {r.ops && r.ops.length > 0 && (
        <div className="mt-2">
          <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground">
            <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
            Structured operations ({r.ops.length})
          </button>
          {open && <pre className="tc scrollbar-thin mt-1 max-h-48 overflow-auto rounded-md bg-background p-2 text-[10.5px] leading-relaxed text-foreground/85">{JSON.stringify(r.ops, null, 2)}</pre>}
        </div>
      )}
      <div className="tc mt-2 text-right text-[10px] text-muted-foreground">
        {r.id} · {r.latencyMs.toFixed(1)} ms
      </div>
    </div>
  );
}

export function InstructionPanel() {
  const { session, run, selection, selectedCaptionId } = useEditor();
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const log = session.log;

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [log.length]);

  const submit = () => {
    const t = text.trim();
    if (!t) return;
    run(t);
    setText("");
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="scrollbar-thin min-h-0 flex-1 space-y-2.5 overflow-y-auto p-3">
        {log.length === 0 && (
          <div className="space-y-3 rounded-lg border border-dashed border-border p-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Sparkles className="h-4 w-4 text-teal" /> Describe an edit
            </div>
            <p className="text-xs leading-relaxed text-muted-foreground">
              Every instruction ends as exactly one of: <span className="text-teal">Applied</span>, <span className="text-warning">Needs clarification</span> or{" "}
              <span className="text-destructive">Rejected</span>. Nothing destructive happens on an ambiguous request.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLES.map((e) => (
                <button key={e} onClick={() => setText(e)} className="rounded-full border border-border bg-background px-2.5 py-1 text-[11px] text-muted-foreground hover:border-primary/60 hover:text-foreground">
                  {e}
                </button>
              ))}
            </div>
          </div>
        )}
        {log.map((r, i) => (
          <ResultCard key={r.id} r={r} latest={i === log.length - 1} />
        ))}
        <div ref={endRef} />
      </div>
      <div className="border-t border-border p-3">
        <p className="mb-1.5 text-[10.5px] leading-snug text-muted-foreground">
          <Info className="mr-1 inline h-3 w-3 align-[-2px]" />
          Times you type are <span className="text-teal">original source time</span>; say “current timeline” for edited time.
        </p>
        <div className="rounded-lg border border-input bg-background focus-within:border-primary">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            placeholder="e.g. Cut the section from 00:30 to 00:45"
            className="block w-full resize-none bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground"
          />
          <div className="flex items-center justify-between px-2 pb-2">
            <span className="text-[10.5px] text-muted-foreground">
              {selection.length ? `${selection.length} word(s) selected` : "No selection"}
              {selectedCaptionId ? ` · caption ${selectedCaptionId}` : ""}
            </span>
            <button onClick={submit} disabled={!text.trim()} className="flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40">
              Run <CornerDownLeft className="h-3 w-3" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
