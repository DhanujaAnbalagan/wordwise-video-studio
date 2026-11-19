import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@/engine/executor";
import { getProject, mediaUrl, putProject, type ProjectRecord } from "@/lib/storage";

export function useProject(id: string) {
  const [project, setProject] = useState<ProjectRecord | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const saveTimer = useRef<number | undefined>(undefined);
  const latest = useRef<ProjectRecord | null>(null);

  useEffect(() => {
    let revoke: string | null = null;
    let alive = true;
    getProject(id)
      .then(async (p) => {
        if (!alive) return;
        if (!p) {
          setError("This project doesn't exist in this browser.");
          return;
        }
        latest.current = p;
        setProject(p);
        const u = await mediaUrl(p);
        if (u.startsWith("blob:")) revoke = u;
        if (alive) setUrl(u);
      })
      .catch((e) => alive && setError((e as Error).message));
    return () => {
      alive = false;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [id]);

  const flush = useCallback(() => {
    window.clearTimeout(saveTimer.current);
    if (latest.current) void putProject(latest.current);
  }, []);

  useEffect(() => {
    window.addEventListener("beforeunload", flush);
    return () => {
      window.removeEventListener("beforeunload", flush);
      flush();
    };
  }, [flush]);

  const update = useCallback((fn: (p: ProjectRecord) => ProjectRecord) => {
    setProject((prev) => {
      if (!prev) return prev;
      const next = { ...fn(prev), updatedAt: Date.now() };
      latest.current = next;
      window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => void putProject(next), 300);
      return next;
    });
  }, []);

  const setSession = useCallback(
    (fn: (s: Session) => Session) =>
      update((p) => {
        const session = fn(p.session);
        return { ...p, session, status: session.transcript.words.length ? "ready" : "needs_transcript" };
      }),
    [update],
  );

  return { project, url, error, setSession, update };
}
