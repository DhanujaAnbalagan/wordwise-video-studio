import { formatTc } from "@/engine/time";
import { cn } from "@/lib/utils";

export function Timecode({ label, ms, total, tone = "primary", className }: { label: string; ms: number | null; total?: number; tone?: "primary" | "teal" | "muted"; className?: string }) {
  const color = tone === "primary" ? "text-primary" : tone === "teal" ? "text-teal" : "text-muted-foreground";
  return (
    <div className={cn("flex flex-col leading-none", className)}>
      <span className={cn("text-[9.5px] font-semibold uppercase tracking-[0.12em]", color)}>{label}</span>
      <span className="tc mt-1 text-[13px] text-foreground">
        {formatTc(ms)}
        {total != null && <span className="text-muted-foreground"> / {formatTc(total)}</span>}
      </span>
    </div>
  );
}
