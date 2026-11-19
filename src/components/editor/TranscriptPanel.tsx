import { AlertTriangle, ArrowUpToLine, Captions, FileJson, Gauge, Scissors, Search, VolumeX, X, Zap } from "lucide-react";
import { memo, useMemo, useRef, useState } from "react";
import { formatTc } from "@/engine/time";
import { LOW_CONFIDENCE, searchPhrase, sentences } from "@/engine/transcript";
import type { WordPlacement } from "@/engine/timeline";
import type { Word } from "@/engine/types";
import { cn } from "@/lib/utils";
import { useEditor, usePlayhead } from "./editor-context";

interface RowProps {
  words: Word[];
  placements: Map<string, WordPlacement>;
  selected: Set<string>;
  activeId: string | null;
  matches: Map<string, "exact" | "related">;
  fx: Map<string, "mute" | "bleep">;
  captioned: Set<string>;
  speakerLabel: string | null;
  onWord: (w: Word, e: React.MouseEvent) => void;
  onSeek: (w: Word) => void;
}

const Row = memo(function Row({ words, placements, selected, activeId, matches, fx, captioned, speakerLabel, onWord, onSeek }: RowProps) {
  const first = words[0];
  const p0 = placements.get(first.id);
  return (
    <div className="group grid grid-cols-[64px_1fr] gap-2 px-3 py-1.5 hover:bg-panel-raised/50">
      <button className="tc pt-0.5 text-left text-[10.5px] leading-tight text-muted-foreground hover:text-teal" onClick={() => onSeek(first)} title="Source time · current timeline time">
        <span className="block text-teal/80">{formatTc(first.start, false)}</span>
        <span className="block">{p0?.visible ? formatTc(p0.tlStart, false) : "cut"}</span>
      </button>
      <div className="text-[13.5px] leading-[1.75]">
        {speakerLabel && <span className="mr-1.5 rounded bg-secondary px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{speakerLabel}</span>}
        {words.map((w) => {
          const p = placements.get(w.id);
          const vis = p?.visible;
          const low = w.confidence != null && w.confidence < LOW_CONFIDENCE;
          const m = matches.get(w.id);
          const f = fx.get(w.id);
          return (
            <span key={w.id}>
              <span
                data-word={w.id}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onWord(w, e);
                }}
                onDoubleClick={() => onSeek(w)}
                title={`${w.id} · source ${formatTc(w.start)}–${formatTc(w.end)} · timeline ${vis ? formatTc(p!.tlStart) : "cut"}${w.speaker ? ` · ${w.speaker}` : ""} · confidence ${w.confidence != null ? `${Math.round(w.confidence * 100)}%` : "not provided"}`}
                className={cn(
                  "cursor-pointer rounded-[3px] px-[1px] transition-colors",
                  !vis && "text-muted-foreground/50 line-through decoration-destructive/70",
                  vis && "hover:bg-secondary",
                  low && "underline decoration-warning decoration-dotted decoration-2 underline-offset-4",
                  m === "exact" && "bg-warning/25 text-foreground",
                  m === "related" && "bg-teal/15",
                  f === "mute" && "text-mute italic",
                  f === "bleep" && "bg-bleep/25 text-bleep",
                  captioned.has(w.id) && "border-b-2 border-warning/70",
                  activeId === w.id && "bg-primary/30 text-foreground",
                  selected.has(w.id) && "bg-primary text-primary-foreground",
                )}
              >
                {w.text}
              </span>{" "}
            </span>
          );
        })}
      </div>
    </div>
  );
});

export function TranscriptPanel() {
  const { transcript, placements, selection, setSelection, engine, state, run, importTranscriptFile } = useEditor();
  const ph = usePlayhead();
  const [query, setQuery] = useState("");
  const [related, setRelated] = useState(true);
  const [capText, setCapText] = useState("");
  const [showCap, setShowCap] = useState(false);
  const anchor = useRef<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const words = transcript.words;

  const rows = useMemo(() => sentences(words).map(([a, b]) => words.slice(a, b + 1)), [words]);
  const multiSpeaker = Object.keys(transcript.speakers).length > 1;
  const found = useMemo(() => (query.trim().length > 1 ? searchPhrase(words, query, related) : []), [query, related, words]);
  const matchMap = useMemo(() => {
    const m = new Map<string, "exact" | "related">();
    for (const f of found) for (const id of f.wordIds) m.set(id, f.kind);
    return m;
  }, [found]);
  const fx = useMemo(() => {
    const m = new Map<string, "mute" | "bleep">();
    for (const e of state.effects) if (m.get(e.wordId) !== "bleep") m.set(e.wordId, e.kind);
    return m;
  }, [state.effects]);
  const captioned = useMemo(() => new Set(state.captions.flatMap((c) => c.wordIds)), [state.captions]);
  const selected = useMemo(() => new Set(selection), [selection]);

  const activeId = useMemo(() => {
    const w = words.find((x) => x.start <= ph.src && ph.src < x.end);
    return w && placements.get(w.id)?.visible ? w.id : null;
  }, [ph.src, words, placements]);

  const onWord = (w: Word, e: React.MouseEvent) => {
    if (e.shiftKey && anchor.current != null) {
      const [a, b] = [anchor.current, w.index].sort((x, y) => x - y);
      setSelection(words.slice(a, b + 1).map((x) => x.id));
    } else if (e.metaKey || e.ctrlKey) {
      setSelection(selected.has(w.id) ? selection.filter((id) => id !== w.id) : [...selection, w.id]);
      anchor.current = w.index;
    } else {
      setSelection(selection.length === 1 && selection[0] === w.id ? [] : [w.id]);
      anchor.current = w.index;
    }
  };
  const onSeek = (w: Word) => engine.seekSrc(w.start);

  const selWords = selection.map((id) => words.find((w) => w.id === id)).filter(Boolean) as Word[];
  const selRange = selWords.length ? { start: Math.min(...selWords.map((w) => w.start)), end: Math.max(...selWords.map((w) => w.end)) } : null;
  const lowCount = words.filter((w) => w.confidence != null && w.confidence < LOW_CONFIDENCE).length;

  if (!words.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <FileJson className="h-8 w-8 text-teal" />
        <p className="text-sm font-medium">No transcript yet</p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Import word-level JSON (VideoFlow format or Whisper verbose_json). Time-based instructions like “remove the first 10 seconds” work without one.
        </p>
        <input ref={fileRef} type="file" accept=".json" className="hidden" onChange={(e) => e.target.files?.[0] && importTranscriptFile(e.target.files[0])} />
        <button onClick={() => fileRef.current?.click()} className="rounded-md bg-teal px-3 py-1.5 text-sm font-medium text-teal-foreground hover:bg-teal/90">
          Import transcript JSON
        </button>
        <p className="text-[11px] text-muted-foreground">In-browser speech-to-text is not available in this version.</p>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-2 border-b border-border p-3">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Transcript</span>
          <span className="tc text-[10.5px] text-muted-foreground">
            {words.length} words · {transcript.provider}
          </span>
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search words, e.g. pricing"
            className="h-8 w-full rounded-md border border-input bg-background pl-8 pr-8 text-sm outline-none focus:border-primary"
          />
          {query && (
            <button aria-label="Clear search" className="absolute right-2 top-2 text-muted-foreground hover:text-foreground" onClick={() => setQuery("")}>
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <label className="flex cursor-pointer items-center gap-1.5">
            <input type="checkbox" checked={related} onChange={(e) => setRelated(e.target.checked)} className="accent-[var(--teal)]" />
            Include related terms
          </label>
          {query.trim().length > 1 && (
            <span>
              <span className="text-warning">{found.filter((f) => f.kind === "exact").length} exact</span>
              {related && <> · <span className="text-teal">{found.filter((f) => f.kind === "related").length} related</span></>}
            </span>
          )}
        </div>
        {found.length > 0 && (
          <div className="scrollbar-thin max-h-28 space-y-0.5 overflow-y-auto">
            {found.map((f, i) => (
              <button key={i} onClick={() => { engine.seekSrc(f.start); setSelection(f.wordIds); }} className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-secondary">
                <span className={cn("h-1.5 w-1.5 rounded-full", f.kind === "exact" ? "bg-warning" : "bg-teal")} />
                <span className="tc text-muted-foreground">{formatTc(f.start, false)}</span>
                <span className="truncate">{f.matched}</span>
                {!placements.get(f.wordIds[0])?.visible && <span className="ml-auto text-[10px] text-destructive">cut</span>}
              </button>
            ))}
          </div>
        )}
        {lowCount > 0 && (
          <div className="flex items-center gap-1.5 text-[11px] text-warning">
            <AlertTriangle className="h-3.5 w-3.5" /> {lowCount} low-confidence word{lowCount > 1 ? "s" : ""} (dotted underline)
          </div>
        )}
      </div>

      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto py-1">
        {rows.map((ws, i) => {
          const prev = rows[i - 1];
          const spk = multiSpeaker && ws[0].speaker && (!prev || prev[0].speaker !== ws[0].speaker) ? (transcript.speakers[ws[0].speaker]?.label ?? ws[0].speaker) : null;
          const inRow = (id: string | null) => (id && ws.some((w) => w.id === id) ? id : null);
          return (
            <Row
              key={ws[0].id}
              words={ws}
              placements={placements}
              selected={selected}
              activeId={inRow(activeId)}
              matches={matchMap}
              fx={fx}
              captioned={captioned}
              speakerLabel={spk}
              onWord={onWord}
              onSeek={onSeek}
            />
          );
        })}
      </div>

      {selRange && (
        <div className="space-y-2 border-t border-border bg-panel-raised p-3">
          <div className="flex items-center justify-between text-xs">
            <span>
              <span className="font-semibold text-primary">{selWords.length} word{selWords.length > 1 ? "s" : ""}</span>
              <span className="tc text-muted-foreground"> · src {formatTc(selRange.start)}–{formatTc(selRange.end)}</span>
            </span>
            <button className="text-muted-foreground hover:text-foreground" onClick={() => setSelection([])} aria-label="Clear selection">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            {[
              { icon: Scissors, label: "Cut", cmd: "cut the selected words" },
              { icon: VolumeX, label: "Mute", cmd: "mute the selected words" },
              { icon: Zap, label: "Bleep", cmd: "bleep the selected words" },
              { icon: Gauge, label: "2x speed", cmd: "speed up the selection to 2x" },
              { icon: ArrowUpToLine, label: "Move to start", cmd: "move the selection to the start" },
            ].map((a) => (
              <button key={a.label} onClick={() => run(a.cmd)} className="flex items-center justify-center gap-1 rounded-md border border-border bg-panel px-2 py-1.5 text-[11.5px] hover:border-primary/60">
                <a.icon className="h-3.5 w-3.5" /> {a.label}
              </button>
            ))}
            <button onClick={() => setShowCap((v) => !v)} className="flex items-center justify-center gap-1 rounded-md border border-border bg-panel px-2 py-1.5 text-[11.5px] hover:border-primary/60">
              <Captions className="h-3.5 w-3.5" /> Caption
            </button>
          </div>
          {showCap && (
            <form
              className="flex gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                if (!capText.trim()) return;
                run(`add caption "${capText.replace(/"/g, "'")}" to the selected words`);
                setCapText("");
                setShowCap(false);
              }}
            >
              <input autoFocus value={capText} onChange={(e) => setCapText(e.target.value)} placeholder="Caption text" className="h-8 flex-1 rounded-md border border-input bg-background px-2 text-sm outline-none focus:border-primary" />
              <button className="rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground">Add</button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}
