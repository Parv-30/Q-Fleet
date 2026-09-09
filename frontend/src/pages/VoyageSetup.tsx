import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import VoyageForm from '../components/VoyageForm';
import { ApiError, runOptimization } from '../api';
import type { OptimizationRequest } from '../types';

/**
 * STATE-PASSING DECISION: `useNavigate(path, { state })` + `useLocation().state`
 * on the Results page, rather than a shared context/provider.
 *
 * Reasoning: the only hand-off this app needs is "the request that was just
 * run" and "the response it returned", flowing in one direction (Voyage
 * Setup -> Results) at the moment a run completes. There's no third page
 * that also needs this data, no need to survive a hard refresh on /results,
 * and re-running is handled by navigating back to /voyage-setup and
 * submitting again -- not by mutating shared state in place. router state
 * covers exactly that with zero extra machinery; a context provider would
 * only pay off if more pages needed to read/write this data or if it had to
 * outlive a single navigation, neither of which applies here.
 */
export default function VoyageSetup() {
  const navigate = useNavigate();
  const [isLoading, setIsLoading] = useState(false);
  const [submitError, setSubmitError] = useState<string>('');

  const runWith = useCallback(
    async (request: OptimizationRequest) => {
      setIsLoading(true);
      setSubmitError('');
      try {
        const response = await runOptimization(request);
        navigate('/results', { state: { request, candidates: response.pareto_front } });
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.message
            : 'An unexpected error occurred while contacting the optimization service.';
        setSubmitError(message);
      } finally {
        setIsLoading(false);
      }
    },
    [navigate]
  );

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 px-4 py-8 sm:px-6 sm:py-10">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">Voyage Setup</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Pin what you already know — vessel, route, cargo, speed, fuel, deadline — and leave
          everything else open. Q-Fleet AI searches the rest and returns a Pareto front of
          non-dominated trade-offs.
        </p>
      </div>

      {submitError && (
        <div role="alert" className="rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          {submitError}
        </div>
      )}

      <VoyageForm onRun={runWith} isLoading={isLoading} />
    </div>
  );
}
