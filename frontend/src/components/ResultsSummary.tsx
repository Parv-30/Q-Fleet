import { Target } from 'lucide-react';
import { useMemo } from 'react';
import { OBJECTIVE_KEYS, OBJECTIVE_META, type OptimizationCandidate } from '../types';
import { computeObjectiveRanges, computeQuickPicks } from '../lib/reasoning';

interface ResultsSummaryProps {
  candidates: OptimizationCandidate[];
  onHighlight: (index: number) => void;
  highlightedIndex: number | null;
}

/**
 * At-a-glance data-driven overview shown above the chart/table: a compact
 * per-objective min/max range row (answers "3 candidates found: cost ranges
 * $X-$Y...") plus a row of "best for X" quick-pick chips that jump straight
 * to a fast starting point for a specific priority -- since a real Pareto
 * front has no single "best", but operators still want one.
 */
export default function ResultsSummary({ candidates, onHighlight, highlightedIndex }: ResultsSummaryProps) {
  const ranges = useMemo(() => computeObjectiveRanges(candidates), [candidates]);
  const quickPicks = useMemo(() => computeQuickPicks(candidates, ranges), [candidates, ranges]);

  if (candidates.length === 0) return null;

  return (
    <section
      aria-labelledby="results-summary-heading"
      className="rounded-xl border border-border bg-card p-4 shadow-sm sm:p-5"
    >
      <h2 id="results-summary-heading" className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {candidates.length} candidate{candidates.length === 1 ? '' : 's'} found
      </h2>

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {OBJECTIVE_KEYS.map((key) => {
          const r = ranges[key];
          const meta = OBJECTIVE_META[key];
          return (
            <div key={key} className="rounded-lg bg-muted/60 px-3 py-2">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{meta.label}</p>
              <p className="font-data text-sm text-foreground">
                {r.min.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                {' – '}
                {r.max.toLocaleString(undefined, { maximumFractionDigits: 1 })}
              </p>
              <p className="text-[10px] text-muted-foreground">{meta.unit}</p>
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
          <Target size={13} aria-hidden="true" />
          Quick picks:
        </span>
        {quickPicks.map((pick) => (
          <button
            key={pick.key}
            type="button"
            onClick={() => onHighlight(pick.candidateIndex)}
            aria-pressed={highlightedIndex === pick.candidateIndex}
            className={`cursor-pointer rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-ring ${
              highlightedIndex === pick.candidateIndex
                ? 'border-accent bg-accent text-accent-foreground'
                : 'border-border bg-background text-muted-foreground hover:text-foreground'
            }`}
          >
            {pick.label}
          </button>
        ))}
        {highlightedIndex !== null && (
          <button
            type="button"
            onClick={() => onHighlight(-1)}
            className="cursor-pointer rounded-lg px-2 py-1.5 text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            Clear highlight
          </button>
        )}
      </div>
    </section>
  );
}
