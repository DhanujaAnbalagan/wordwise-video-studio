/** Formats milliseconds as mm:ss.mmm (or h:mm:ss.mmm). */
export function formatTc(ms: number | null | undefined, withMs = true): string {
  if (ms == null || !Number.isFinite(ms)) return "--:--";
  const neg = ms < 0;
  let t = Math.round(Math.abs(ms));
  const h = Math.floor(t / 3_600_000);
  t %= 3_600_000;
  const m = Math.floor(t / 60_000);
  t %= 60_000;
  const s = Math.floor(t / 1000);
  const r = t % 1000;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  const core = `${h ? `${h}:` : ""}${mm}:${ss}${withMs ? `.${String(r).padStart(3, "0")}` : ""}`;
  return (neg ? "-" : "") + core;
}

export const TIME_PATTERN =
  "(\\d+:\\d{1,2}(?::\\d{1,2})?(?:\\.\\d+)?|\\d+(?:\\.\\d+)?\\s*(?:ms|milliseconds?|s|secs?|seconds?|m|mins?|minutes?)?(?![\\dx:]))";

/** Parses "1:20", "00:30.5", "1:02:03", "90s", "1.5 minutes", "42" into ms. */
export function parseTime(raw: string): number | null {
  const tok = raw.trim().toLowerCase();
  if (!tok) return null;
  if (tok.includes(":")) {
    const parts = tok.split(":");
    if (parts.length > 3 || parts.some((p) => p === "" || Number.isNaN(Number(p)))) return null;
    let secs = 0;
    for (const p of parts) secs = secs * 60 + Number(p);
    return Math.round(secs * 1000);
  }
  const m = tok.match(/^(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|secs?|seconds?|m|mins?|minutes?)?$/);
  if (!m) return null;
  const n = Number(m[1]);
  const unit = m[2] ?? "s";
  if (unit.startsWith("ms") || unit.startsWith("milli")) return Math.round(n);
  if (unit.startsWith("m")) return Math.round(n * 60_000);
  return Math.round(n * 1000);
}
