import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { formatDistanceToNow } from "date-fns";
import { Clapperboard, FileJson, FlaskConical, Loader2, Play, Plus, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Brand } from "@/components/vf/Brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatTc } from "@/engine/time";
import { formatBytes } from "@/lib/media";
import { createDemoProject, createUploadProject, DEMOS } from "@/lib/projects";
import { deleteProject, listProjects, type ProjectRecord } from "@/lib/storage";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Projects — VideoFlow AI" },
      { name: "description", content: "Upload an MP4 or MOV, open a demo, and edit video by typing instructions." },
      { property: "og:title", content: "Projects — VideoFlow AI" },
      { property: "og:description", content: "Upload a video or open a demo project to start prompt-based editing." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Dashboard,
});

function Dashboard() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectRecord[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [video, setVideo] = useState<File | null>(null);
  const [transcript, setTranscript] = useState<File | null>(null);
  const [name, setName] = useState("");
  const videoRef = useRef<HTMLInputElement>(null);
  const trRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    listProjects().then(setProjects).catch(() => setProjects([]));
  }, []);
  useEffect(refresh, [refresh]);

  async function openDemo(key: string) {
    setBusy(key);
    try {
      const p = await createDemoProject(key);
      navigate({ to: "/projects/$id", params: { id: p.id } });
    } catch (e) {
      toast.error((e as Error).message);
      setBusy(null);
    }
  }

  async function upload() {
    if (!video) return;
    setBusy("upload");
    try {
      const p = await createUploadProject(video, name, transcript);
      toast.success("Project created", { description: `SHA-256 ${p.media.sha256.slice(0, 16)}…` });
      navigate({ to: "/projects/$id", params: { id: p.id } });
    } catch (e) {
      const err = e as Error & { code?: string };
      toast.error(err.code ? `${err.code}` : "Upload failed", { description: err.message });
      setBusy(null);
    }
  }

  return (
    <div className="min-h-screen">
      <header className="flex h-14 items-center justify-between border-b border-border bg-panel px-6">
        <Brand />
        <nav className="flex items-center gap-1 text-sm">
          <Link to="/worked-example" className="rounded-md px-3 py-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
            Worked example
          </Link>
        </nav>
      </header>

      <main className="mx-auto grid max-w-7xl gap-8 px-6 py-10 lg:grid-cols-[380px_1fr]">
        <section className="space-y-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Edit video by typing what you want.</h1>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Every edit is anchored to the original words, so captions and effects follow their content through cuts,
              speed changes, reorders, undo and restore.
            </p>
          </div>

          <div className="rounded-xl border border-border bg-panel p-5">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
              <Plus className="h-4 w-4 text-primary" /> New project
            </div>
            <div className="space-y-3">
              <Input placeholder="Project name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
              <input ref={videoRef} type="file" accept=".mp4,.mov,video/mp4,video/quicktime" className="hidden" onChange={(e) => setVideo(e.target.files?.[0] ?? null)} />
              <input ref={trRef} type="file" accept=".json,application/json" className="hidden" onChange={(e) => setTranscript(e.target.files?.[0] ?? null)} />
              <button
                onClick={() => videoRef.current?.click()}
                className="flex w-full items-center gap-3 rounded-lg border border-dashed border-input bg-background/50 px-4 py-4 text-left text-sm hover:border-primary/60"
              >
                <Upload className="h-5 w-5 shrink-0 text-primary" />
                <span className="min-w-0">
                  <span className="block truncate font-medium">{video ? video.name : "Choose an MP4 or MOV video"}</span>
                  <span className="text-xs text-muted-foreground">{video ? formatBytes(video.size) : "The original file is kept unchanged"}</span>
                </span>
              </button>
              <button
                onClick={() => trRef.current?.click()}
                className="flex w-full items-center gap-3 rounded-lg border border-dashed border-input bg-background/50 px-4 py-3 text-left text-sm hover:border-teal/60"
              >
                <FileJson className="h-5 w-5 shrink-0 text-teal" />
                <span className="min-w-0">
                  <span className="block truncate font-medium">{transcript ? transcript.name : "Transcript JSON (optional)"}</span>
                  <span className="text-xs text-muted-foreground">VideoFlow or Whisper word-level JSON</span>
                </span>
              </button>
              <Button className="w-full" disabled={!video || !!busy} onClick={upload}>
                {busy === "upload" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Clapperboard className="h-4 w-4" />}
                {busy === "upload" ? "Hashing & reading video…" : "Create project"}
              </Button>
            </div>
          </div>

          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Demo media</div>
            {DEMOS.map((d) => (
              <button
                key={d.key}
                disabled={!!busy}
                onClick={() => openDemo(d.key)}
                className="flex w-full items-center gap-3 rounded-lg border border-border bg-panel px-4 py-3 text-left hover:border-primary/50 disabled:opacity-60"
              >
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-primary/15 text-primary">
                  {busy === d.key ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{d.name}</span>
                  <span className="block text-xs text-muted-foreground">{d.blurb}</span>
                </span>
              </button>
            ))}
            <p className="text-xs text-muted-foreground">Synthetic voices — no real person's voice or face.</p>
          </div>
        </section>

        <section>
          <div className="mb-3 flex items-end justify-between">
            <h2 className="text-sm font-semibold">Recent projects</h2>
            <span className="text-xs text-muted-foreground">Saved in this browser</span>
          </div>
          <div className="overflow-hidden rounded-xl border border-border bg-panel">
            <div className="grid grid-cols-[1fr_120px_90px_80px_130px_40px] gap-3 border-b border-border px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              <span>Project</span>
              <span>Status</span>
              <span>Duration</span>
              <span>Revisions</span>
              <span>Last modified</span>
              <span />
            </div>
            {projects === null && <div className="px-4 py-10 text-center text-sm text-muted-foreground">Loading…</div>}
            {projects?.length === 0 && (
              <div className="flex flex-col items-center gap-2 px-4 py-16 text-center">
                <FlaskConical className="h-6 w-6 text-muted-foreground" />
                <p className="text-sm text-muted-foreground">No projects yet. Upload a video or open a demo.</p>
              </div>
            )}
            {projects?.map((p) => (
              <div key={p.id} className="grid grid-cols-[1fr_120px_90px_80px_130px_40px] items-center gap-3 border-b border-border px-4 py-3 text-sm last:border-0 hover:bg-panel-raised">
                <Link to="/projects/$id" params={{ id: p.id }} className="min-w-0">
                  <span className="block truncate font-medium hover:text-primary">{p.name}</span>
                  <span className="tc block truncate text-[11px] text-muted-foreground">
                    {p.media.fileName} · sha256 {p.media.sha256.slice(0, 12)}…
                  </span>
                </Link>
                <span>
                  {p.status === "ready" ? (
                    <span className="rounded bg-teal/15 px-1.5 py-0.5 text-[11px] font-medium text-teal">Ready</span>
                  ) : (
                    <span className="rounded bg-warning/15 px-1.5 py-0.5 text-[11px] font-medium text-warning">No transcript</span>
                  )}
                </span>
                <span className="tc text-xs">{formatTc(p.media.durationMs, false)}</span>
                <span className="tc text-xs">{p.session.history.revisions.length - 1}</span>
                <span className="text-xs text-muted-foreground">{formatDistanceToNow(p.updatedAt, { addSuffix: true })}</span>
                <button
                  aria-label={`Delete ${p.name}`}
                  className="grid h-7 w-7 place-items-center rounded text-muted-foreground hover:bg-destructive/15 hover:text-destructive"
                  onClick={async () => {
                    if (!confirm(`Delete “${p.name}” from this browser?`)) return;
                    await deleteProject(p.id);
                    refresh();
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
