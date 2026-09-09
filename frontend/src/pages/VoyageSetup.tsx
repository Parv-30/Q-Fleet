import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import VoyageForm from '../components/VoyageForm';
import PortGlobe from '../components/PortGlobe';
import { ApiError, getPorts, getRoutes, runOptimization } from '../api';
import { useDarkMode } from '../useDarkMode';
import type { OptimizationRequest, Port, RouteOption } from '../types';

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
  const [isDark] = useDarkMode();
  const [isLoading, setIsLoading] = useState(false);
  const [submitError, setSubmitError] = useState<string>('');

  // Route state lives here (not inside VoyageForm) so both the dropdowns
  // and PortGlobe are controlled views onto the SAME state -- clicking a
  // marker on the globe and picking from a <select> both flow through
  // these same setters, keeping the two perfectly in sync.
  const [ports, setPorts] = useState<Port[]>([]);
  const [portsError, setPortsError] = useState('');
  const [origin, setOrigin] = useState('');
  const [destination, setDestination] = useState('');
  const [routeOptions, setRouteOptions] = useState<RouteOption[]>([]);
  const [routeLoading, setRouteLoading] = useState(false);
  const [routeError, setRouteError] = useState('');
  const [selectedRouteIdx, setSelectedRouteIdx] = useState<number | 'either'>('either');

  // Load port catalog once.
  useEffect(() => {
    const controller = new AbortController();
    getPorts(controller.signal)
      .then(setPorts)
      .catch((err) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setPortsError(err instanceof ApiError ? err.message : 'Could not load the port catalog.');
      });
    return () => controller.abort();
  }, []);

  // Fetch real route option(s) whenever both ends are chosen.
  useEffect(() => {
    if (!origin || !destination) {
      setRouteOptions([]);
      setRouteError('');
      return;
    }
    const controller = new AbortController();
    setRouteLoading(true);
    setRouteError('');
    getRoutes(origin, destination, controller.signal)
      .then((options) => {
        setRouteOptions(options);
        setSelectedRouteIdx(options.length > 1 ? 'either' : 0);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setRouteOptions([]);
        setRouteError(err instanceof ApiError ? err.message : 'Could not compute a route for this port pair.');
      })
      .finally(() => setRouteLoading(false));
    return () => controller.abort();
  }, [origin, destination]);

  // Globe marker click -> fills whichever dropdown PortGlobe decided (see
  // its onSelectPort role heuristic); picking the SAME port again for the
  // role it already holds clears it, so re-clicking a marker toggles it off.
  const handleSelectPort = useCallback(
    (portName: string, role: 'origin' | 'destination') => {
      if (role === 'origin') {
        setOrigin((prev) => (prev === portName ? '' : portName));
      } else {
        setDestination((prev) => (prev === portName ? '' : portName));
      }
    },
    []
  );

  // Route actually displayed right now (respects the "let optimizer choose"
  // / specific-option radio state), fed to the globe's real-path layer.
  const activeRoute: RouteOption | null =
    routeOptions.length === 0
      ? null
      : selectedRouteIdx === 'either'
        ? routeOptions[0]
        : routeOptions[selectedRouteIdx] ?? routeOptions[0];

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

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
        <VoyageForm
          onRun={runWith}
          isLoading={isLoading}
          ports={ports}
          portsError={portsError}
          origin={origin}
          destination={destination}
          onOriginChange={setOrigin}
          onDestinationChange={setDestination}
          routeOptions={routeOptions}
          routeLoading={routeLoading}
          routeError={routeError}
          selectedRouteIdx={selectedRouteIdx}
          onSelectedRouteIdxChange={setSelectedRouteIdx}
        />

        <div className="flex flex-col gap-2 xl:sticky xl:top-20 xl:self-start">
          <div className="rounded-xl border border-border bg-card p-3 shadow-sm sm:p-4">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-foreground">
              Port Map
            </h2>
            <p className="mb-3 text-xs text-muted-foreground">
              Click a port to set origin/destination, or pick from the form — both stay in sync.
            </p>
            <PortGlobe
              origin={origin}
              destination={destination}
              onSelectPort={handleSelectPort}
              selectedRoute={activeRoute}
              isDark={isDark}
              className="overflow-hidden rounded-lg"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
