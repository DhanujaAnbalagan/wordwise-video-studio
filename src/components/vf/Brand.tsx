import { Link } from "@tanstack/react-router";

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2.5 text-foreground">
      <span className="relative grid h-7 w-7 place-items-center rounded-md bg-primary/15 ring-1 ring-primary/40">
        <span className="absolute left-1.5 top-1.5 h-4 w-1 rounded-sm bg-primary" />
        <span className="absolute left-3 top-2.5 h-2.5 w-1 rounded-sm bg-teal" />
        <span className="absolute left-[18px] top-1 h-5 w-1 rounded-sm bg-primary/70" />
      </span>
      {!compact && (
        <span className="text-[15px] font-semibold tracking-tight">
          VideoFlow <span className="text-teal">AI</span>
        </span>
      )}
    </Link>
  );
}
