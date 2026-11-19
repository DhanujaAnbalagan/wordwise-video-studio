import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { CheckCircle2, Loader2, Play, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Brand } from "@/components/vf/Brand";
import { Button } from "@/components/ui/button";
import { runInstruction } from "@/engine/executor";
import { createDemoProject } from "@/lib/projects";
import { putProject } from "@/lib/storage";
import { runWorkedExample, WORKED_INSTRUCTIONS } from "@/lib/worked-example";

export const Route = createFileRoute("/worked-example")({
  head: () => ({
    meta: [
      { title: "Worked example — VideoFlow AI" },
      { name: "description", content: "The eight-step caption-anchoring demonstration, executed live against the timeline engine." },
      { property: "og:title", content: "Worked example — VideoFlow AI" },
      { property: "og:description", content: "Captions follow their words through cuts, speed changes, restore and undo — with evidence." },
      { property: "og:type", content: "article" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: WorkedExample,
});

function WorkedExample() {
  const { steps } = useMemo(() => runWorkedExample(), []);
  const passed = steps.filter((s) => s.pass).length;
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  async function replay() {
    setBusy(true);
    try {
      const p = await createDemoProject("lecture");
      let s = p.session;
      for (const ins of WORKED_INSTRUCTIONS) s = runInstruction(s, ins, {});
      await putProject({ ...p, name: "Worked example (lecture)", session: s, updatedAt: Date.now() });
      navigate({ to: "/projects/$id", params: { id: p.id } });
    } catch (e) {
      toast.error((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen">
      <header className="flex h-14 items-center justify-between border-b border-border bg-panel px-6">
        <Brand />
        <Link to="/" className="text-sm text-muted-foreground hover:text-foreground">
          Projects
        </Link>
      </header>
      <main className="mx-auto max-w-4xl px-6 py-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Worked example</h1>
            <p className="mt-1 text-sm text-muted-foreground">Executed just now in your browser against the real engine and the synthetic lecture transcript.</p>
          </div>
          <div className="flex items-center gap-3">
            <span className={passed === steps.length ? "tc text-sm font-semibold text-teal" : "tc text-sm font-semibold text-destructive"}>
              {passed}/{steps.length} steps passed
            </span>
            <Button onClick={replay} disabled={busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Replay in the editor
            </Button>
          </div>
        </div>
        <ol className="mt-8 space-y-3">
          {steps.map((s) => (
            <li key={s.n} className="rounded-xl border border-border bg-panel p-4">
              <div className="flex items-start gap-3">
                <span className="tc grid h-7 w-7 shrink-0 place-items-center rounded-full bg-secondary text-xs font-semibold">{s.n}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <h2 className="text-sm font-semibold">{s.title}</h2>
                    {s.pass ? <CheckCircle2 className="h-4 w-4 shrink-0 text-teal" /> : <XCircle className="h-4 w-4 shrink-0 text-destructive" />}
                  </div>
                  {s.instruction && (
                    <p className="tc mt-1 text-xs text-primary">
                      &gt; {s.instruction} <span className="text-muted-foreground">→ {s.status}</span>
                    </p>
                  )}
                  <ul className="mt-2 space-y-0.5">
                    {s.evidence.filter(Boolean).map((e, i) => (
                      <li key={i} className="text-xs leading-relaxed text-muted-foreground">
                        {e}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </li>
          ))}
        </ol>
      </main>
    </div>
  );
}
