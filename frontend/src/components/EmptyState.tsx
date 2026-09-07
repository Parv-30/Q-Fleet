import { Compass } from 'lucide-react';

export default function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-card p-10 text-center shadow-sm">
      <span className="flex h-14 w-14 items-center justify-center rounded-full bg-muted text-primary">
        <Compass size={28} aria-hidden="true" />
      </span>
      <h2 className="text-base font-semibold text-foreground">No optimization run yet</h2>
      <p className="max-w-md text-sm text-muted-foreground">
        Set your iterations, swarm size, and optional seed above, then click{' '}
        <span className="font-medium text-foreground">Run Optimization</span>. Q-Fleet AI will
        search vessel, route, speed and fuel combinations and return a Pareto front of
        non-dominated trade-offs — plotted on a scatter chart and listed in a sortable table
        below.
      </p>
    </div>
  );
}
