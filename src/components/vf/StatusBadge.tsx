import { cn } from "@/lib/utils";
import type { InstructionStatus } from "@/engine/types";

const MAP: Record<InstructionStatus, { label: string; cls: string }> = {
  applied: { label: "Applied", cls: "bg-teal/15 text-teal ring-teal/30" },
  needs_clarification: { label: "Needs clarification", cls: "bg-warning/15 text-warning ring-warning/30" },
  rejected: { label: "Rejected", cls: "bg-destructive/15 text-destructive ring-destructive/30" },
};

export function StatusBadge({ status, className }: { status: InstructionStatus; className?: string }) {
  const m = MAP[status];
  return (
    <span className={cn("inline-flex items-center rounded px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide ring-1", m.cls, className)}>
      {m.label}
    </span>
  );
}
