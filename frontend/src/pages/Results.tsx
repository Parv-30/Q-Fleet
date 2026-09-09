import { useCallback, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import EmptyState from '../components/EmptyState';
import ErrorState from '../components/ErrorState';
import LoadingState from '../components/LoadingState';
import ParetoChart from '../components/ParetoChart';
import ResultsSummary from '../components/ResultsSummary';
import ResultsTable from '../components/ResultsTable';
import { ApiError, runOptimization } from '../api';
import { useDarkMode } from '../useDarkMode';
import type { OptimizationCandidate, OptimizationRequest } from '../types';

interface LocationState {
  request: OptimizationRequest;
  candidates: OptimizationCandidate[];
}

export default function Results() {
  const location = useLocation();
  const navigate = useNavigate();
  const [isDark] = useDarkMode();
  const state = location.state as LocationState | null;

  const [candidates, setCandidates] = useState<OptimizationCandidate[]>(state?.candidates ?? []);
  const [lastRequest] = useState<OptimizationRequest | null>(state?.request ?? null);
  const [status, setStatus] = useState<'success' | 'loading' | 'error'>('success');
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [highlightedIndex, setHighlightedIndex] = useState<number | null>(null);

  const handleRetry = useCallback(async () => {
    if (!lastRequest) return;
    setStatus('loading');
    setErrorMessage('');
    try {
      const response = await runOptimization(lastRequest);
      setCandidates(response.pareto_front);
      setStatus('success');
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : 'An unexpected error occurred while contacting the optimization service.';
      setErrorMessage(message);
      setStatus('error');
    }
  }, [lastRequest]);

  const handleHighlight = useCallback((index: number) => {
    setHighlightedIndex(index === -1 ? null : index);
  }, []);

  // Nothing was passed via router state -- most likely a direct visit to
  // /results with no run behind it. Send the user back to set one up rather
  // than rendering an empty results page.
  if (!state) {
    return (
      <div className="mx-auto flex max-w-[1400px] flex-col gap-6 px-4 py-8 sm:px-6 sm:py-10">
        <EmptyState />
        <Link
          to="/voyage-setup"
          className="flex w-fit cursor-pointer items-center gap-2 rounded-lg bg-primary px-5 py-2.5 font-semibold text-primary-foreground shadow-sm transition-all duration-200 hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
        >
          <ArrowLeft size={16} aria-hidden="true" />
          Go to Voyage Setup
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-6 px-4 py-8 sm:px-6 sm:py-10">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">Results</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Pareto-front comparison for the voyage constraints you set.
          </p>
        </div>
        <button
          type="button"
          onClick={() => navigate('/voyage-setup')}
          className="flex w-fit cursor-pointer items-center gap-2 rounded-lg border-2 border-primary px-4 py-2 text-sm font-semibold text-primary transition-all duration-200 hover:bg-primary hover:text-primary-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
        >
          <ArrowLeft size={16} aria-hidden="true" />
          Edit voyage setup
        </button>
      </div>

      {status === 'loading' && <LoadingState />}
      {status === 'error' && <ErrorState message={errorMessage} onRetry={handleRetry} />}
      {status === 'success' && candidates.length === 0 && <EmptyState />}
      {status === 'success' && candidates.length > 0 && (
        <div className="flex flex-col gap-4">
          <ResultsSummary
            candidates={candidates}
            onHighlight={handleHighlight}
            highlightedIndex={highlightedIndex}
          />
          <ParetoChart candidates={candidates} highlightedIndex={highlightedIndex} isDark={isDark} />
          <ResultsTable candidates={candidates} highlightedIndex={highlightedIndex} />
        </div>
      )}
    </div>
  );
}
