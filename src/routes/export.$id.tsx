import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, CheckCircle2, Copy, Download, FileJson, Loader2, Play, ShieldCheck, Square, TriangleAlert, XCircle } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Brand } from "@/components/vf/Brand";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { currentState } from "@/engine/executor";
import { head } from "@/engine/history";
import { buildManifest, buildRenderPlan, ffmpegCommand } from "@/engine/render";
import { formatTc } from "@/engine/time";
import { useProject } from "@/hooks/use-project";
import { renderInBrowser, type RenderOutput } from "@/lib/export-render";
import { formatBytes, sha256Hex } from "@/lib/media";

export const Route = createFileRoute("/export/$id")({
  head: () => ({
    meta: [
      { title: "Export — VideoFlow AI" },
      { name: "description", content: "Render the edited timeline, verify its duration and download the video with its manifest." },
      { property: "og:title", content: "Export — VideoFlow AI" },
      { property: "og:description", content: "Render, verify and download the edited video with a full output manifest." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: ExportPage,
});

type JobState = "queued" | "running" | "succeeded" | "failed";
const FRAME_MS = 1000 / 30;

function download(name: string, data: Blob | string, type = "application/json") {
  const blob = typeof data === "string" ? new Blob([data], { type }) : data;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

function ExportPage() {
  const { id } = Route.useParams();
  const { project, url, error } = useProject(id);
  const [job, setJob] = useState<JobState>("queued");
  const [progress, setProgress] = useState(0);
  const [label, setLabel] = useState("");
  const [out, setOut] = useState<RenderOutput | null>(null);
  const [outUrl, setOutUrl] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [srcCheck, setSrcCheck] = useState<"pending" | "match" | "mismatch" | null>(null);
  const abort = useRef<AbortController | null>(null);

  const plan = useMemo(() => (project ? buildRenderPlan(currentState(project.session), project.session.transcript) : null), [project]);
  const rev = project ? head(project.session.history) : null;
  const manifest = useMemo(() => {
    if (!project || !plan || !rev) return null;
    return buildManifest({
      projectName: project.name,
      revisionId: rev.id,
      plan,
      transcript: project.session.transcript,
      source: { fileName: project.media.fileName, sha256: project.media.sha256, durationMs: project.media.durationMs },
      isDemo: project.isDemo,
      timings: out
        ? { renderWallMs: Math.round(out.wallMs), expectedDurationMs: Math.round(out.expectedDurationMs), measuredDurationMs: Math.round(out.measuredDurationMs) }
        : { render: "not run yet" },
      extraWarnings: out && Math.abs(out.measuredDurationMs - out.expectedDurationMs) > FRAME_MS ? [`Measured duration differs from the plan by ${Math.round(out.measuredDurationMs - out.expectedDurationMs)} ms (more than one frame).`] : [],
    });
  }, [project, plan, rev, out]);

  if (error) return <div className="grid min-h-screen place-items-center text-sm text-destructive">{error}</div>;
  if (!project || !plan || !url || !manifest || !rev)
    return (
      <div className="grid min-h-screen place-items-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );

  const cmd = ffmpegCommand(plan, project.media.fileName, "output.mp4");
  const delta = out ? out.measuredDurationMs - out.expectedDurationMs : 0;
  const withinFrame = out ? Math.abs(delta) <= FRAME_MS : false;

  async function start() {
    if (!plan) return;
    setJob("running");
    setFailure(null);
    setOut(null);
    setSrcCheck(null);
    setProgress(0);
    abort.current = new AbortController();
    try {
      const r = await renderInBrowser(url!, plan, (p, l) => { setProgress(p); setLabel(l); }, abort.current.signal);
      setOut(r);
      setOutUrl(URL.createObjectURL(r.blob));
      setJob("succeeded");
      setSrcCheck("pending");
      const blob = await (await fetch(url!)).blob();
      setSrcCheck((await sha256Hex(blob)) === project!.media.sha256 ? "match" : "mismatch");
    } catch (e) {
      setJob("failed");
      setFailure((e as Error).message);
    }
  }

  const ext = out?.mime.includes("mp4") ? "mp4" : "webm";

  return (
    <div className="min-h-screen">
      <header className="flex h-12 items-center gap-4 border-b border-border bg-panel px-4">
        <Brand compact />
        <Link to="/projects/$id" params={{ id }} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" /> Back to editor
        </Link>
        <span className="text-sm font-semibold">{project.name}</span>
        <span className="tc text-[11px] text-muted-foreground">
          {rev.id} · {plan.timelineHash.slice(0, 12)}
        </span>
      </header>

      <main className="mx-auto grid max-w-7xl gap-6 px-6 py-8 lg:grid-cols-[1fr_420px]">
        <section className="space-y-6">
          <div className="rounded-xl border border-border bg-panel p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h1 className="text-lg font-semibold">Render</h1>
                <p className="text-xs text-muted-foreground">
                  Expected length <span className="tc text-foreground">{formatTc(plan.expectedDurationMs)}</span> · {plan.segments.length} segments · {plan.captions.length} caption spans · {plan.audio.length} audio effects
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className="tc rounded bg-secondary px-2 py-1 text-[11px] uppercase">{job}</span>
                {job === "running" ? (
                  <Button variant="secondary" onClick={() => abort.current?.abort()}>
                    <Square className="h-4 w-4" /> Cancel
                  </Button>
                ) : (
                  <Button onClick={start}>
                    <Play className="h-4 w-4" /> {job === "succeeded" ? "Render again" : "Render in browser"}
                  </Button>
                )}
              </div>
            </div>
            {(job === "running" || job === "succeeded") && (
              <div className="mt-4 space-y-1.5">
                <Progress value={progress} />
                <div className="tc flex justify-between text-[11px] text-muted-foreground">
                  <span>{label}</span>
                  <span>{Math.round(progress)}%</span>
                </div>
              </div>
            )}
            <p className="mt-3 text-[11.5px] leading-relaxed text-muted-foreground">
              Renders in real time inside this tab (keep it visible). Output is WebM recorded from the edited timeline; the original file is only read, never written.
            </p>
            {failure && (
              <div className="mt-3 flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                <XCircle className="h-4 w-4" /> {failure}
              </div>
            )}
          </div>

          {out && outUrl && (
            <div className="rounded-xl border border-border bg-panel p-5">
              <div className="grid gap-5 md:grid-cols-[1fr_260px]">
                <video src={outUrl} controls className="w-full rounded-md bg-background ring-1 ring-border" />
                <div className="space-y-3 text-sm">
                  <div className="flex items-center gap-2 font-semibold">
                    {withinFrame ? <CheckCircle2 className="h-4 w-4 text-teal" /> : <TriangleAlert className="h-4 w-4 text-warning" />}
                    Verification
                  </div>
                  <dl className="tc grid grid-cols-[1fr_auto] gap-y-1 text-[11.5px]">
                    <dt className="text-muted-foreground">Output exists</dt>
                    <dd>{formatBytes(out.blob.size)}</dd>
                    <dt className="text-muted-foreground">Expected</dt>
                    <dd>{formatTc(out.expectedDurationMs)}</dd>
                    <dt className="text-muted-foreground">Measured</dt>
                    <dd>{formatTc(out.measuredDurationMs)}</dd>
                    <dt className="text-muted-foreground">Difference</dt>
                    <dd className={withinFrame ? "text-teal" : "text-warning"}>
                      {delta >= 0 ? "+" : ""}
                      {Math.round(delta)} ms {withinFrame ? "(≤ 1 frame)" : "(> 1 frame)"}
                    </dd>
                    <dt className="text-muted-foreground">Render time</dt>
                    <dd>{(out.wallMs / 1000).toFixed(1)} s</dd>
                    <dt className="text-muted-foreground">A/V sync</dt>
                    <dd className="text-muted-foreground">not measured</dd>
                  </dl>
                  <div className="flex items-center gap-2 text-[11.5px]">
                    <ShieldCheck className={srcCheck === "match" ? "h-4 w-4 text-teal" : "h-4 w-4 text-muted-foreground"} />
                    {srcCheck === "pending" && "Re-hashing original…"}
                    {srcCheck === "match" && "Original unchanged (SHA-256 matches)"}
                    {srcCheck === "mismatch" && <span className="text-destructive">Original hash mismatch!</span>}
                  </div>
                  <Button className="w-full" onClick={() => download(`${project.name.replace(/\W+/g, "-")}-${rev.id}.${ext}`, out.blob)}>
                    <Download className="h-4 w-4" /> Download video
                  </Button>
                </div>
              </div>
            </div>
          )}

          <div className="rounded-xl border border-border bg-panel p-5">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold">Equivalent FFmpeg command</h2>
              <button
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                onClick={() => {
                  void navigator.clipboard.writeText(cmd);
                  toast.success("Copied");
                }}
              >
                <Copy className="h-3.5 w-3.5" /> Copy
              </button>
            </div>
            <p className="mb-2 text-[11.5px] text-muted-foreground">Generated from the same render plan, for a full-quality MP4 render on a machine with FFmpeg. Not executed here.</p>
            <pre className="tc scrollbar-thin max-h-72 overflow-auto rounded-md bg-background p-3 text-[10.5px] leading-relaxed text-foreground/85">{cmd}</pre>
          </div>
        </section>

        <aside className="space-y-6">
          <div className="rounded-xl border border-border bg-panel p-5">
            <h2 className="mb-3 text-sm font-semibold">Warnings</h2>
            {manifest.warnings.length === 0 ? (
              <p className="text-xs text-muted-foreground">None.</p>
            ) : (
              <ul className="space-y-1.5">
                {manifest.warnings.map((w, i) => (
                  <li key={i} className="flex gap-2 text-xs text-warning">
                    <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {w}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="rounded-xl border border-border bg-panel p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold">Output manifest</h2>
              <div className="flex gap-1">
                <button onClick={() => download(`manifest-${rev.id}.json`, JSON.stringify(manifest, null, 2))} className="flex items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground">
                  <FileJson className="h-3.5 w-3.5" /> Manifest
                </button>
                <button onClick={() => download(`render-plan-${rev.id}.json`, JSON.stringify(plan, null, 2))} className="flex items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground">
                  <FileJson className="h-3.5 w-3.5" /> Plan
                </button>
              </div>
            </div>
            <pre className="tc scrollbar-thin max-h-[520px] overflow-auto rounded-md bg-background p-3 text-[10.5px] leading-relaxed text-foreground/85">{JSON.stringify(manifest, null, 2)}</pre>
          </div>
        </aside>
      </main>
    </div>
  );
}
