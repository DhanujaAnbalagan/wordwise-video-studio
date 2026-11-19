import { RotateCcw } from "lucide-react";
import { formatTc } from "@/engine/time";
import { timelineDuration } from "@/engine/timeline";
import { cn } from "@/lib/utils";
import { useEditor } from "./editor-context";

export function HistoryPanel() {
  const { session, run } = useEditor();
  const h = session.history;
  const headId = h.undo[h.undo.length - 1];
  const applied = new Set(h.undo);
  const redoable = new Set(h.redo);
  return (
    <div className="scrollbar-thin h-full overflow-y-auto p-3">
      <p className="mb-3 text-[11.5px] leading-relaxed text-muted-foreground">
        One revision per accepted instruction. Restoring creates a new revision, so it can be undone. The original video is never modified.
      </p>
      <ol className="space-y-1.5">
        {[...h.revisions].reverse().map((r) => {
          const isHead = r.id === headId;
          const status = isHead ? "current" : applied.has(r.id) ? "applied" : redoable.has(r.id) ? "undone (redo)" : "off-branch";
          return (
            <li key={r.id} className={cn("rounded-lg border p-2.5", isHead ? "border-primary/60 bg-primary/10" : "border-border bg-panel", !applied.has(r.id) && !isHead && "opacity-70")}>
              <div className="flex items-center justify-between gap-2">
                <span className="tc text-xs font-semibold text-primary">{r.id}</span>
                <span className={cn("text-[10px] font-medium uppercase tracking-wide", isHead ? "text-primary" : redoable.has(r.id) ? "text-warning" : "text-muted-foreground")}>{status}</span>
              </div>
              <p className="mt-1 text-[12.5px] leading-snug">{r.label}</p>
              <div className="tc mt-1 flex items-center justify-between text-[10.5px] text-muted-foreground">
                <span>
                  {formatTc(timelineDuration(r.state.segments))} · {r.state.segments.length} seg · {r.hash.slice(0, 10)}
                </span>
                {!isHead && (
                  <button onClick={() => run(`restore revision ${r.index}`)} className="flex items-center gap-1 rounded px-1.5 py-0.5 text-foreground hover:bg-secondary">
                    <RotateCcw className="h-3 w-3" /> Restore
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
