import { useCallback, useState } from 'react';
import EmptyState from './components/EmptyState';
import ErrorState from './components/ErrorState';
import Header from './components/Header';
import LoadingState from './components/LoadingState';
import ParetoChart from './components/ParetoChart';
import ResultsSummary from './components/ResultsSummary';
import ResultsTable from './components/ResultsTable';
import VoyageForm from './components/VoyageForm';
import { ApiError, runOptimization } from './api';
import { useDarkMode } from './useDarkMode';
import type { OptimizationCandidate, OptimizationRequest } from './types';

type Status = 'idle' | 'loading' | 'success' | 'error';

function App() {
  const [isDark, toggleDark] = useDarkMode();
  const [status, setStatus] = useState<Status>('idle');
  const [candidates, setCandidates] = useState<OptimizationCandidate[]>([]);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [lastRequest, setLastRequest] = useState<OptimizationRequest | null>(null);
  const [highlightedIndex, setHighlightedIndex] = useState<number | null>(null);

  const runWith = useCallback(async (request: OptimizationRequest) => {
    setLastRequest(request);
    setStatus('loading');
    setErrorMessage('');
    setHighlightedIndex(null);
    try {
      const response = await runOptimization(request);
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
  }, []);

  const handleRetry = useCallback(() => {
    if (lastRequest) runWith(lastRequest);
  }, [lastRequest, runWith]);

  const handleHighlight = useCallback((index: number) => {
    setHighlightedIndex(index === -1 ? null : index);
  }, []);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <Header isDark={isDark} onToggleDark={toggleDark} />

      <main className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-5 sm:px-6 sm:py-6">
        <VoyageForm onRun={runWith} isLoading={status === 'loading'} />

        {status === 'idle' && <EmptyState />}
        {status === 'loading' && <LoadingState />}
        {status === 'error' && <ErrorState message={errorMessage} onRetry={handleRetry} />}
        {status === 'success' && (
          <div className="flex flex-col gap-4">
            <ResultsSummary
              candidates={candidates}
              onHighlight={handleHighlight}
              highlightedIndex={highlightedIndex}
            />
            <ParetoChart candidates={candidates} highlightedIndex={highlightedIndex} />
            <ResultsTable candidates={candidates} highlightedIndex={highlightedIndex} />
          </div>
        )}
      </main>

      <footer className="mx-auto max-w-[1600px] px-4 py-6 text-center text-xs text-muted-foreground sm:px-6">
        Q-Fleet AI Dashboard — decision support for fleet voyage optimization
      </footer>
    </div>
  );
}

export default App;
