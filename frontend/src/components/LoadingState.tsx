import { Loader2 } from 'lucide-react';

export default function LoadingState() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card p-10 text-center shadow-sm"
    >
      <Loader2 size={32} className="animate-spin text-primary" aria-hidden="true" />
      <p className="text-sm font-medium text-foreground">Running QPSO + NSGA-II optimization…</p>
      <p className="text-xs text-muted-foreground">
        Searching vessel, route, speed and fuel combinations. This can take several seconds.
      </p>
    </div>
  );
}
