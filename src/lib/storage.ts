/**
 * Browser-only persistence (IndexedDB): project documents in "projects", uploaded video
 * blobs in "media". Original uploads are stored once and never modified.
 */
import type { Session } from "@/engine/executor";

export interface MediaInfo {
  fileName: string;
  mime: string;
  sizeBytes: number;
  sha256: string;
  durationMs: number;
  width: number;
  height: number;
  demoUrl?: string;
}

export interface ProjectRecord {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
  status: "ready" | "needs_transcript";
  isDemo: boolean;
  media: MediaInfo;
  session: Session;
}

const DB = "videoflow";
let dbPromise: Promise<IDBDatabase> | null = null;

function open(): Promise<IDBDatabase> {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("projects")) db.createObjectStore("projects", { keyPath: "id" });
        if (!db.objectStoreNames.contains("media")) db.createObjectStore("media");
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return dbPromise;
}

function tx<T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return open().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(store, mode);
        const req = fn(t.objectStore(store));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      }),
  );
}

export const listProjects = () =>
  tx<ProjectRecord[]>("projects", "readonly", (s) => s.getAll()).then((all) => all.sort((a, b) => b.updatedAt - a.updatedAt));
export const getProject = (id: string) => tx<ProjectRecord | undefined>("projects", "readonly", (s) => s.get(id));
export const putProject = (p: ProjectRecord) => tx("projects", "readwrite", (s) => s.put(p));
export const putMedia = (id: string, blob: Blob) => tx("media", "readwrite", (s) => s.put(blob, id));
export const getMedia = (id: string) => tx<Blob | undefined>("media", "readonly", (s) => s.get(id));
export async function deleteProject(id: string) {
  await tx("projects", "readwrite", (s) => s.delete(id));
  await tx("media", "readwrite", (s) => s.delete(id));
}

export function newId(prefix = "prj") {
  return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
}

/** Returns a playable URL for the project's original media. */
export async function mediaUrl(p: ProjectRecord): Promise<string> {
  if (p.media.demoUrl) return p.media.demoUrl;
  const blob = await getMedia(p.id);
  if (!blob) throw new Error("The original video for this project is missing from this browser.");
  return URL.createObjectURL(blob);
}
