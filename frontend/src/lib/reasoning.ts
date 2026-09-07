// Client-side "reasoning" computation: simple relative-ranking logic over
// the returned Pareto front, no ML needed. Answers the user's explicit
// complaint -- "a reasoning [for] what each gives best" -- by comparing each
// candidate's objectives against the min/max across the SAME returned set.
import { OBJECTIVE_KEYS, OBJECTIVE_META, type ObjectiveKey, type OptimizationCandidate } from '../types';

export interface ObjectiveRange {
  min: number;
  max: number;
}

export type ObjectiveRanges = Record<ObjectiveKey, ObjectiveRange>;

export function computeObjectiveRanges(candidates: OptimizationCandidate[]): ObjectiveRanges {
  const ranges = {} as ObjectiveRanges;
  for (const key of OBJECTIVE_KEYS) {
    const values = candidates.map((c) => c.objectives[key]);
    ranges[key] = {
      min: values.length ? Math.min(...values) : 0,
      max: values.length ? Math.max(...values) : 0,
    };
  }
  return ranges;
}

/** 0 = worst in the set, 1 = best in the set, for this objective's direction. */
function normalizedScore(value: number, key: ObjectiveKey, range: ObjectiveRange): number {
  const { min, max } = range;
  if (max === min) return 1;
  const frac = (value - min) / (max - min);
  return OBJECTIVE_META[key].direction === 'minimize' ? 1 - frac : frac;
}

/**
 * One short plain-language sentence describing where this candidate stands
 * relative to the rest of the returned Pareto front. Picks the objective
 * it's BEST at (highest normalized score) and, if there's a clear runner-up
 * downside, mentions the trade-off -- e.g. "Lowest cost, but 34% higher
 * emissions than the cleanest option."
 */
export function describeCandidate(
  candidate: OptimizationCandidate,
  ranges: ObjectiveRanges,
  allCandidates: OptimizationCandidate[]
): string {
  if (allCandidates.length <= 1) return 'Only candidate returned.';

  const scored = OBJECTIVE_KEYS.map((key) => ({
    key,
    score: normalizedScore(candidate.objectives[key], key, ranges[key]),
  })).sort((a, b) => b.score - a.score);

  const best = scored[0];
  const worst = scored[scored.length - 1];

  const bestLabel = OBJECTIVE_META[best.key].label.toLowerCase();
  const isBestInSet = best.score >= 0.999;

  const leadPhrase = isBestInSet ? `Best ${bestLabel} in this set` : `Strong ${bestLabel}`;

  // If this candidate is meaningfully behind on its weakest dimension,
  // mention the trade-off with a relative percentage against the best
  // performer in the set for that same objective.
  if (worst.score < 0.5) {
    const worstKey = worst.key;
    const dir = OBJECTIVE_META[worstKey].direction;
    const range = ranges[worstKey];
    const bestValueInSet = dir === 'minimize' ? range.min : range.max;
    const thisValue = candidate.objectives[worstKey];
    const pct =
      bestValueInSet !== 0 ? Math.abs(((thisValue - bestValueInSet) / bestValueInSet) * 100) : 0;
    const worseWord = dir === 'minimize' ? 'higher' : 'lower';
    const worstLabel = OBJECTIVE_META[worstKey].label.toLowerCase();
    if (pct >= 1) {
      return `${leadPhrase}, but ${pct.toFixed(0)}% ${worseWord} ${worstLabel} than the best in this set.`;
    }
  }

  if (isBestInSet) return `${leadPhrase}.`;
  return `${leadPhrase}, moderate on the rest.`;
}

export interface QuickPick {
  key: string;
  label: string;
  candidateIndex: number;
}

/**
 * "Best for X" quick-filter entries: for each headline objective, find the
 * index (within `candidates`) of the candidate that scores best on it, plus
 * a "Best balance" pick (highest average normalized score across all 6).
 */
export function computeQuickPicks(candidates: OptimizationCandidate[], ranges: ObjectiveRanges): QuickPick[] {
  if (candidates.length === 0) return [];

  const headline: { key: ObjectiveKey; label: string }[] = [
    { key: 'operating_cost', label: 'Lowest cost' },
    { key: 'lifecycle_ghg', label: 'Lowest emissions' },
    { key: 'reliability', label: 'Highest reliability' },
  ];

  const picks: QuickPick[] = headline.map(({ key, label }) => {
    let bestIdx = 0;
    let bestScore = -Infinity;
    candidates.forEach((c, idx) => {
      const score = normalizedScore(c.objectives[key], key, ranges[key]);
      if (score > bestScore) {
        bestScore = score;
        bestIdx = idx;
      }
    });
    return { key, label, candidateIndex: bestIdx };
  });

  // Best balance: highest mean normalized score across all objectives.
  let balanceIdx = 0;
  let balanceScore = -Infinity;
  candidates.forEach((c, idx) => {
    const mean =
      OBJECTIVE_KEYS.reduce((sum, key) => sum + normalizedScore(c.objectives[key], key, ranges[key]), 0) /
      OBJECTIVE_KEYS.length;
    if (mean > balanceScore) {
      balanceScore = mean;
      balanceIdx = idx;
    }
  });
  picks.push({ key: 'balance', label: 'Best balance', candidateIndex: balanceIdx });

  return picks;
}
