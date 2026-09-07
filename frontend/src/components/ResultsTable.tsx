import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { OBJECTIVE_KEYS, OBJECTIVE_META, type ObjectiveKey, type OptimizationCandidate } from '../types';
import { computeObjectiveRanges, describeCandidate } from '../lib/reasoning';

interface ResultsTableProps {
  candidates: OptimizationCandidate[];
  highlightedIndex?: number | null;
}

type SortKey = 'vessel' | 'route' | 'speed' | 'fuel' | ObjectiveKey;

interface SortState {
  key: SortKey;
  direction: 'asc' | 'desc';
}

const BASE_COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'vessel', label: 'Vessel' },
  { key: 'route', label: 'Route' },
  { key: 'speed', label: 'Speed (kn)' },
  { key: 'fuel', label: 'Fuel' },
];

function getSortValue(candidate: OptimizationCandidate, key: SortKey): string | number {
  if (key === 'vessel' || key === 'route' || key === 'fuel') return candidate[key];
  if (key === 'speed') return candidate.speed;
  return candidate.objectives[key as ObjectiveKey];
}

export default function ResultsTable({ candidates, highlightedIndex = null }: ResultsTableProps) {
  const [sort, setSort] = useState<SortState>({ key: 'fuel_consumption', direction: 'asc' });
  const rowRefs = useRef<Map<number, HTMLTableRowElement>>(new Map());

  const ranges = useMemo(() => computeObjectiveRanges(candidates), [candidates]);

  // Track original index alongside each candidate so highlighting/reasoning
  // survives re-sorting.
  const indexed = useMemo(
    () => candidates.map((c, originalIndex) => ({ c, originalIndex })),
    [candidates]
  );

  const sorted = useMemo(() => {
    const copy = [...indexed];
    copy.sort((a, b) => {
      const av = getSortValue(a.c, sort.key);
      const bv = getSortValue(b.c, sort.key);
      let cmp: number;
      if (typeof av === 'number' && typeof bv === 'number') {
        cmp = av - bv;
      } else {
        cmp = String(av).localeCompare(String(bv));
      }
      return sort.direction === 'asc' ? cmp : -cmp;
    });
    return copy;
  }, [indexed, sort]);

  useEffect(() => {
    if (highlightedIndex === null) return;
    const row = rowRefs.current.get(highlightedIndex);
    row?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [highlightedIndex]);

  const toggleSort = (key: SortKey) => {
    setSort((prev) =>
      prev.key === key
        ? { key, direction: prev.direction === 'asc' ? 'desc' : 'asc' }
        : { key, direction: 'asc' }
    );
  };

  const SortIcon = ({ column }: { column: SortKey }) => {
    if (sort.key !== column) return <ArrowUpDown size={12} className="opacity-40" aria-hidden="true" />;
    return sort.direction === 'asc' ? (
      <ArrowUp size={12} aria-hidden="true" />
    ) : (
      <ArrowDown size={12} aria-hidden="true" />
    );
  };

  const headerButtonClass =
    'flex w-full cursor-pointer items-center gap-1 text-left font-semibold text-muted-foreground hover:text-foreground transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-ring rounded';

  return (
    <section
      aria-labelledby="results-table-heading"
      className="rounded-xl border border-border bg-card p-4 shadow-sm sm:p-5"
    >
      <h2 id="results-table-heading" className="mb-4 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        Candidate Results ({candidates.length})
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border">
              {BASE_COLUMNS.map((col) => (
                <th key={col.key} scope="col" className="px-3 py-2 text-left">
                  <button
                    type="button"
                    onClick={() => toggleSort(col.key)}
                    className={headerButtonClass}
                    aria-label={`Sort by ${col.label}`}
                  >
                    {col.label}
                    <SortIcon column={col.key} />
                  </button>
                </th>
              ))}
              {OBJECTIVE_KEYS.map((key) => (
                <th key={key} scope="col" className="px-3 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => toggleSort(key)}
                    className={`${headerButtonClass} justify-end`}
                    aria-label={`Sort by ${OBJECTIVE_META[key].label}`}
                    title={`${OBJECTIVE_META[key].direction === 'minimize' ? 'Lower is better' : 'Higher is better'}`}
                  >
                    {OBJECTIVE_META[key].label}
                    <SortIcon column={key} />
                  </button>
                  <div className="mt-0.5 font-normal normal-case text-muted-foreground">
                    ({OBJECTIVE_META[key].unit})
                  </div>
                </th>
              ))}
              <th scope="col" className="px-3 py-2 text-left font-semibold text-muted-foreground">
                Reasoning
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(({ c, originalIndex }) => {
              const isHighlighted = highlightedIndex === originalIndex;
              return (
                <tr
                  key={`${c.vessel}-${c.route}-${c.speed}-${c.fuel}-${originalIndex}`}
                  ref={(el) => {
                    if (el) rowRefs.current.set(originalIndex, el);
                    else rowRefs.current.delete(originalIndex);
                  }}
                  className={`transition-colors duration-150 ${
                    isHighlighted ? 'bg-accent/15' : 'border-b border-border hover:bg-muted/60'
                  }`}
                >
                  {/* border-collapse tables don't render a ring/outline cleanly on <tr> --
                      each cell is its own box, so the "full row" border is built by giving
                      every <td> a matching top+bottom border, with the outer edges (left on
                      the first cell, right on the last) closing the rectangle. */}
                  <td className={`px-3 py-2 text-foreground ${isHighlighted ? 'border-y-2 border-l-2 border-accent' : ''}`}>{c.vessel}</td>
                  <td className={`px-3 py-2 text-muted-foreground ${isHighlighted ? 'border-y-2 border-accent' : ''}`}>{c.route}</td>
                  <td className={`px-3 py-2 font-data text-foreground ${isHighlighted ? 'border-y-2 border-accent' : ''}`}>{c.speed}</td>
                  <td className={`px-3 py-2 uppercase text-foreground ${isHighlighted ? 'border-y-2 border-accent' : ''}`}>{c.fuel}</td>
                  {OBJECTIVE_KEYS.map((key) => (
                    <td
                      key={key}
                      className={`px-3 py-2 text-right font-data text-foreground ${isHighlighted ? 'border-y-2 border-accent' : ''}`}
                    >
                      {c.objectives[key].toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                  ))}
                  <td className={`max-w-[220px] px-3 py-2 text-xs text-muted-foreground ${isHighlighted ? 'border-y-2 border-r-2 border-accent' : ''}`}>
                    {describeCandidate(c, ranges, candidates)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
