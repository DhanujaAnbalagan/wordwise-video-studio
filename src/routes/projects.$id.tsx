import { createFileRoute, Link } from "@tanstack/react-router";
import { Download, Loader2, Redo2, Undo2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { EditorContext, type EditorCtx } from "@/components/editor/editor-context";
import { HistoryPanel } from "@/components/editor/HistoryPanel";
import { InspectorPanel } from "@/components/editor/InspectorPanel";
import { InstructionPanel } from "@/components/editor/InstructionPanel";
import { PreviewPlayer } from "@/components/editor/PreviewPlayer";
import { TimelineTracks } from "@/components/editor/TimelineTracks";
import { TranscriptPanel } from "@/components/editor/TranscriptPanel";
import { Brand } from "@/components/vf/Brand";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { chooseOption, createSession, currentState, runInstruction } from "@/engine/executor";
import { head } from "@/engine/history";
import { layout, placeEffects, placeWords, resolveCaptions, timelineDuration } from "@/engine/timeline";
import { importTranscript, transcriptWarnings } from "@/engine/transcript";
import { useProject } from "@/hooks/use-project";
import { PlaybackEngine } from "@/lib/playback";

export const Route = createFileRoute("/projects/$id")({
  head: () => ({
    meta: [
      { title: "Editor — VideoFlow AI" },
      { name: "description", content: "Prompt-based video editing workspace with word-level transcript and multi-track timeline." },
      { property: "og:title", content: "Editor — VideoFlow AI" },
      { property: "og:description", content: "Edit by instruction; captions stay anchored to their original words." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: EditorPage,
});

function EditorPage() {
  const { id } = Route.useParams();
  const { project, url, error, setSession } = useProject(id);
  const engineRef = useRef<PlaybackEngine | null>(null);
  if (!engineRef.current) engineRef.current = new PlaybackEngine();
  const engine = engineRef.current;
  const [selection, setSelection] = useState<string[]>([]);
  const [selectedCaptionId, setSelectedCaptionId] = useState<string | null>(null);

  useEffect(() => () => engine.dispose(), [engine]);

  const session = project?.session;
  const derived = useMemo(() => {
    if (!session) return null;
    const state = currentState(session);
    const placed = layout(state.segments);
    const words = session.transcript.words;
    const placements = placeWords(placed, words);
    const wordsById = new Map(words.map((w) => [w.id, w]));
    return {
      state,
      placed,
      placements,
      wordsById,
      captions: resolveCaptions(state, wordsById, placements),
      effects: placeEffects(state),
      warnings: transcriptWarnings(session.transcript),
      durationTl: timelineDuration(state.segments),
    };
  }, [session]);

  useEffect(() => {
    if (derived) engine.setTimeline(derived.state.segments, derived.state.effects);
  }, [derived, engine]);

  useEffect(() => {
    if (selectedCaptionId && derived && !derived.state.captions.some((c) => c.id === selectedCaptionId)) setSelectedCaptionId(null);
  }, [derived, selectedCaptionId]);

  const sessionRef = useRef(session);
  sessionRef.current = session;

  const run = useCallback(
    (text: string) => {
      const cur = sessionRef.current;
      if (!cur) return;
      const next = runInstruction(cur, text, { selection, selectedCaptionId, playheadSrcMs: engine.snapshot.src });
      sessionRef.current = next;
      setSession(() => next);
      const r = next.log[next.log.length - 1];
      if (r.status === "applied") toast.success(r.message);
      else if (r.status === "rejected") toast.error(r.error?.code ?? "Rejected", { description: r.message });
      else toast.warning("Needs clarification", { description: r.message });
    },
    [setSession, selection, selectedCaptionId, engine],
  );

  const choose = useCallback(
    (resultId: string, idx: number) => {
      const cur = sessionRef.current;
      if (!cur) return;
      const next = chooseOption(cur, resultId, idx);
      sessionRef.current = next;
      setSession(() => next);
      const r = next.log[next.log.length - 1];
      if (r.status === "applied") toast.success(r.message);
      else toast.error(r.error?.code ?? "Rejected", { description: r.message });
    },
    [setSession],
  );

  const importTranscriptFile = useCallback(
    async (file: File) => {
      try {
        const t = importTranscript(JSON.parse(await file.text()), project?.media.durationMs);
        setSession((s) => {
          if (s.history.revisions.length > 1 && !confirm("Importing a transcript starts a fresh edit history. Continue?")) return s;
          return createSession(t, s.history.revisions[0].state.sourceDurationMs, Date.now());
        });
        toast.success(`Imported ${t.words.length} words`);
      } catch (e) {
        const err = e as Error & { code?: string };
        toast.error(err.code ?? "Invalid transcript", { description: err.message });
      }
    },
    [setSession, project?.media.durationMs],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.code === "Space") {
        e.preventDefault();
        engine.toggle();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        run(e.shiftKey ? "redo" : "undo");
      } else if (e.key === "Escape") setSelection([]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [engine, run]);

  if (error)
    return (
      <div className="grid min-h-screen place-items-center p-6 text-center">
        <div>
          <p className="text-sm text-destructive">{error}</p>
          <Link to="/" className="mt-3 inline-block text-sm text-primary hover:underline">
            Back to projects
          </Link>
        </div>
      </div>
    );
  if (!project || !session || !derived || !url)
    return (
      <div className="grid min-h-screen place-items-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );

  const ctx: EditorCtx = {
    project,
    session,
    transcript: session.transcript,
    ...derived,
    engine,
    selection,
    setSelection,
    selectedCaptionId,
    setSelectedCaptionId,
    run,
    choose,
    importTranscriptFile,
  };
  const h = session.history;
  const cur = head(h);

  return (
    <EditorContext.Provider value={ctx}>
      <div className="grid h-screen grid-rows-[48px_1fr_272px] overflow-hidden">
        <header className="flex items-center gap-4 border-b border-border bg-panel px-4">
          <Brand compact />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{project.name}</div>
            <div className="tc truncate text-[10.5px] text-muted-foreground">
              {cur.id} · hash {cur.hash.slice(0, 12)} · source sha256 {project.media.sha256.slice(0, 10)}…
            </div>
          </div>
          <div className="ml-auto flex items-center gap-1">
            <button disabled={h.undo.length <= 1} onClick={() => run("undo")} className="flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs hover:bg-secondary disabled:opacity-40" title="Undo (Ctrl+Z)">
              <Undo2 className="h-4 w-4" /> Undo
            </button>
            <button disabled={!h.redo.length} onClick={() => run("redo")} className="flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs hover:bg-secondary disabled:opacity-40" title="Redo (Ctrl+Shift+Z)">
              <Redo2 className="h-4 w-4" /> Redo
            </button>
            <Link to="/export/$id" params={{ id: project.id }} className="ml-2 flex h-8 items-center gap-1.5 rounded-md bg-teal px-3 text-xs font-semibold text-teal-foreground hover:bg-teal/90">
              <Download className="h-4 w-4" /> Export
            </Link>
          </div>
        </header>

        <div className="grid min-h-0 grid-cols-[340px_1fr_380px]">
          <aside className="min-h-0 border-r border-border bg-panel">
            <TranscriptPanel />
          </aside>
          <main className="min-h-0 min-w-0">
            <PreviewPlayer url={url} />
          </main>
          <aside className="flex min-h-0 flex-col border-l border-border bg-panel">
            <Tabs defaultValue="ai" className="flex min-h-0 flex-1 flex-col gap-0">
              <TabsList className="m-2 grid grid-cols-3 bg-background">
                <TabsTrigger className="text-xs data-[state=active]:bg-panel-raised" value="ai">AI edit</TabsTrigger>
                <TabsTrigger className="text-xs data-[state=active]:bg-panel-raised" value="history">History ({h.revisions.length - 1})</TabsTrigger>
                <TabsTrigger className="text-xs data-[state=active]:bg-panel-raised" value="inspect">Inspector</TabsTrigger>
              </TabsList>
              <TabsContent value="ai" className="mt-0 min-h-0 flex-1">
                <InstructionPanel />
              </TabsContent>
              <TabsContent value="history" className="mt-0 min-h-0 flex-1">
                <HistoryPanel />
              </TabsContent>
              <TabsContent value="inspect" className="mt-0 min-h-0 flex-1">
                <InspectorPanel />
              </TabsContent>
            </Tabs>
          </aside>
        </div>

        <section className="min-h-0 border-t border-border">
          <TimelineTracks />
        </section>
      </div>
    </EditorContext.Provider>
  );
}
