import { Loader2, Play, ChevronDown, Ship, Route as RouteIcon, Package, Gauge, Fuel as FuelIcon, CloudSun, CalendarClock, Settings2, AlertTriangle, Wind, Waves, Thermometer } from 'lucide-react';
import { type FormEvent, useEffect, useId, useMemo, useState } from 'react';
import { getWeather, ApiError } from '../api';
import {
  FLEET_CATALOG,
  FUEL_LABELS,
  FUEL_TYPES,
  VESSEL_TYPES,
  VESSEL_TYPE_LABELS,
  type FuelType,
  type OptimizationConstraints,
  type OptimizationRequest,
  type Port,
  type RouteOption,
  type VesselType,
  type WeatherSample,
} from '../types';

// DESIGN DECISION: multi-card single page, not a step-by-step wizard.
//
// The user's explicit complaint was "i dont want a linear webpage ... more
// interactive more data driven more options". A wizard (Vessel -> Route ->
// Cargo -> ...) is still fundamentally linear -- it just paginates the same
// top-to-bottom flow and hides sections from view until you click Next,
// which is arguably a worse fit for someone who wants to see everything
// available and jump between decisions freely (e.g. pick fuel first, then
// go back and loosen the vessel constraint). Grouped cards on one page,
// each independently collapsible/expandable, let a user scan the whole
// decision space (matching the architecture diagram's parallel input
// categories: Vessel, Route, Cargo, Speed, Fuel, Weather, Operational) and
// jump straight to any section, in any order, without a forced sequence --
// that is what makes this "not linear" rather than a re-skinned wizard.
// Advanced (QPSO knobs) is demoted into its own collapsed-by-default card.

interface VoyageFormProps {
  onRun: (request: OptimizationRequest) => void;
  isLoading: boolean;
  // Route state is lifted to the parent (VoyageSetup) so the globe and this
  // form share one source of truth instead of each fetching/holding its own
  // copy -- see VoyageSetup.tsx for the two-way sync wiring.
  ports: Port[];
  portsError: string;
  origin: string;
  destination: string;
  onOriginChange: (origin: string) => void;
  onDestinationChange: (destination: string) => void;
  routeOptions: RouteOption[];
  routeLoading: boolean;
  routeError: string;
  selectedRouteIdx: number | 'either';
  onSelectedRouteIdxChange: (idx: number | 'either') => void;
}

type VesselMode = 'open' | 'type' | 'exact';
type SpeedMode = 'open' | 'exact' | 'range';

const SPEED_MIN = 8;
const SPEED_MAX = 24;

function Card({
  title,
  icon,
  subtitle,
  children,
  defaultOpen = true,
  collapsible = false,
}: {
  title: string;
  icon: React.ReactNode;
  subtitle?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  collapsible?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const headingId = useId();
  const bodyId = useId();

  return (
    <section
      aria-labelledby={headingId}
      className="rounded-xl border border-border bg-card p-4 shadow-sm sm:p-5"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 text-primary" aria-hidden="true">
            {icon}
          </span>
          <div>
            <h3 id={headingId} className="text-sm font-semibold uppercase tracking-wide text-foreground">
              {title}
            </h3>
            {subtitle && <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>}
          </div>
        </div>
        {collapsible && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-controls={bodyId}
            className="flex cursor-pointer items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-muted-foreground transition-colors duration-150 hover:bg-muted hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {open ? 'Hide' : 'Show'}
            <ChevronDown
              size={14}
              aria-hidden="true"
              className={`transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
            />
          </button>
        )}
      </div>
      {open && (
        <div id={bodyId} className="mt-4">
          {children}
        </div>
      )}
    </section>
  );
}

const fieldLabelClass = 'text-sm font-medium text-foreground';
const helperClass = 'text-xs text-muted-foreground';
const inputClass =
  'rounded-lg border border-border bg-background px-3 py-2 font-data text-sm text-foreground transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-60';
const selectClass =
  'cursor-pointer rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-60';

export default function VoyageForm({
  onRun,
  isLoading,
  ports,
  portsError,
  origin,
  destination,
  onOriginChange,
  onDestinationChange,
  routeOptions,
  routeLoading,
  routeError,
  selectedRouteIdx,
  onSelectedRouteIdxChange,
}: VoyageFormProps) {
  // --- Vessel ---
  const [vesselMode, setVesselMode] = useState<VesselMode>('open');
  const [vesselId, setVesselId] = useState<string>('');
  const [vesselType, setVesselType] = useState<VesselType>('container');

  // --- Cargo ---
  const [cargoTonnes, setCargoTonnes] = useState<string>('');

  // --- Speed ---
  const [speedMode, setSpeedMode] = useState<SpeedMode>('open');
  const [speedExact, setSpeedExact] = useState<string>('');
  const [speedMin, setSpeedMin] = useState<string>('');
  const [speedMax, setSpeedMax] = useState<string>('');

  // --- Fuel ---
  const [selectedFuels, setSelectedFuels] = useState<Set<FuelType>>(new Set(FUEL_TYPES));

  // --- Weather ---
  const [weatherOverride, setWeatherOverride] = useState(false);
  const [windSpeed, setWindSpeed] = useState<string>('');
  const [waveHeight, setWaveHeight] = useState<string>('');
  const [temperature, setTemperature] = useState<string>('');
  const [currentSpeed, setCurrentSpeed] = useState<string>('');
  const [weatherSamples, setWeatherSamples] = useState<WeatherSample[]>([]);
  const [weatherLoading, setWeatherLoading] = useState(false);
  const [weatherError, setWeatherError] = useState<string>('');

  // --- Deadline ---
  const [deadline, setDeadline] = useState<string>('');

  // --- Advanced (QPSO) ---
  const [iterations, setIterations] = useState<number>(20);
  const [swarmSize, setSwarmSize] = useState<number>(15);
  const [seed, setSeed] = useState<number | null>(null);

  const vesselIdSelectId = useId();
  const vesselTypeSelectId = useId();
  const originId = useId();
  const destinationId = useId();
  const cargoId = useId();
  const speedExactId = useId();
  const speedMinId = useId();
  const speedMaxId = useId();
  const windId = useId();
  const waveId = useId();
  const tempId = useId();
  const currentId = useId();
  const deadlineId = useId();
  const iterationsId = useId();
  const swarmId = useId();
  const seedId = useId();

  // When both origin and destination are chosen, fetch real live weather
  // sampled along the route (current conditions + 5-day forecast per
  // waypoint). This hits Open-Meteo through 2 network hops, so it can take
  // a few seconds -- the Weather card shows a loading state meanwhile.
  useEffect(() => {
    if (!origin || !destination) {
      setWeatherSamples([]);
      setWeatherError('');
      return;
    }
    const controller = new AbortController();
    setWeatherLoading(true);
    setWeatherError('');
    getWeather(origin, destination, controller.signal)
      .then((samples) => {
        setWeatherSamples(samples);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setWeatherSamples([]);
        setWeatherError(err instanceof ApiError ? err.message : 'Could not load live weather for this route.');
      })
      .finally(() => setWeatherLoading(false));
    return () => controller.abort();
  }, [origin, destination]);

  // Representative summary across all sampled waypoints along the route:
  // simple averages for current conditions, and a per-day average across
  // waypoints for the 5-day forecast (each sample already covers the same
  // 5 upcoming dates).
  const weatherSummary = useMemo(() => {
    const usable = weatherSamples.filter((s) => !s.error);
    const samples = usable.length > 0 ? usable : weatherSamples;
    if (samples.length === 0) return null;

    const avg = (values: number[]) => values.reduce((a, b) => a + b, 0) / values.length;

    const current = {
      temperature: avg(samples.map((s) => s.current.temperature)),
      wind_speed: avg(samples.map((s) => s.current.wind_speed)),
      wave_height: avg(samples.map((s) => s.current.wave_height)),
      current_speed: avg(samples.map((s) => s.current.current_speed)),
    };

    const dayCount = Math.max(...samples.map((s) => s.forecast.length), 0);
    const forecast = Array.from({ length: dayCount }, (_, i) => {
      const daySamples = samples.map((s) => s.forecast[i]).filter((d): d is NonNullable<typeof d> => Boolean(d));
      if (daySamples.length === 0) return null;
      return {
        date: daySamples[0].date,
        temp_max: avg(daySamples.map((d) => d.temp_max)),
        temp_min: avg(daySamples.map((d) => d.temp_min)),
        wind_speed_max: avg(daySamples.map((d) => d.wind_speed_max)),
        wave_height_max: avg(daySamples.map((d) => d.wave_height_max)),
      };
    }).filter((d): d is NonNullable<typeof d> => d !== null);

    const anyPointErrored = weatherSamples.some((s) => s.error);

    return { current, forecast, anyPointErrored, pointCount: weatherSamples.length };
  }, [weatherSamples]);

  // Prefill the manual override fields from the live summary the first time
  // it becomes available, so a user who opens "Override weather manually"
  // starts from real numbers rather than blank inputs. Never overwrites a
  // value the user has already typed.
  useEffect(() => {
    if (!weatherSummary) return;
    setWindSpeed((prev) => (prev === '' ? weatherSummary.current.wind_speed.toFixed(1) : prev));
    setWaveHeight((prev) => (prev === '' ? weatherSummary.current.wave_height.toFixed(1) : prev));
    setTemperature((prev) => (prev === '' ? weatherSummary.current.temperature.toFixed(1) : prev));
    setCurrentSpeed((prev) => (prev === '' ? weatherSummary.current.current_speed.toFixed(1) : prev));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weatherSummary]);

  const toggleFuel = (fuel: FuelType) => {
    setSelectedFuels((prev) => {
      const next = new Set(prev);
      if (next.has(fuel)) next.delete(fuel);
      else next.add(fuel);
      return next;
    });
  };

  const allFuelsSelected = selectedFuels.size === FUEL_TYPES.length;
  const noFuelsSelected = selectedFuels.size === 0;

  const constraintsSummary = useMemo(() => {
    const parts: string[] = [];
    if (vesselMode === 'exact' && vesselId) parts.push(`vessel ${vesselId}`);
    else if (vesselMode === 'type') parts.push(`vessel type ${VESSEL_TYPE_LABELS[vesselType]}`);
    if (origin || destination) parts.push(`${origin || 'any origin'} → ${destination || 'any destination'}`);
    if (cargoTonnes) parts.push(`${cargoTonnes} t cargo`);
    if (speedMode === 'exact' && speedExact) parts.push(`${speedExact} kn`);
    else if (speedMode === 'range' && (speedMin || speedMax)) parts.push(`${speedMin || SPEED_MIN}-${speedMax || SPEED_MAX} kn`);
    if (!allFuelsSelected) parts.push(`${selectedFuels.size} fuel${selectedFuels.size === 1 ? '' : 's'}`);
    if (weatherOverride) parts.push('manual weather');
    if (deadline) parts.push('deadline set');
    return parts.length > 0 ? parts.join(' · ') : 'fully open — optimizer explores everything';
  }, [
    vesselMode,
    vesselId,
    vesselType,
    origin,
    destination,
    cargoTonnes,
    speedMode,
    speedExact,
    speedMin,
    speedMax,
    allFuelsSelected,
    selectedFuels.size,
    weatherOverride,
    deadline,
  ]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();

    const constraints: OptimizationConstraints = {};

    if (vesselMode === 'exact' && vesselId) {
      constraints.vessel_id = vesselId;
    } else if (vesselMode === 'type') {
      constraints.vessel_type = vesselType;
    }

    // NOTE: /api/routes returns RouteOption (origin/destination/distance_km/via)
    // with no route_id, so there is no backend handle to pin ONE of two
    // options (e.g. "Suez only, not Cape") -- OptimizationConstraints.route_id
    // is an advanced pin for a route_id a caller already knows some other
    // way, which this catalog lookup doesn't produce. Sending origin+destination
    // narrows QPSO to exactly the 1-2 real options for that pair (matching the
    // "narrow, not pin" semantics in schemas.py); QPSO still freely chooses
    // between Suez/Cape when 2 exist, which is also exactly what "let optimizer
    // choose between these" means, so that selection is a display/expectation
    // aid here rather than a request field. A future backend change exposing
    // route_id per option would let a specific single-route pick send route_id.
    if (origin) constraints.origin = origin;
    if (destination) constraints.destination = destination;

    if (cargoTonnes) constraints.cargo_tonnes = Number(cargoTonnes);

    if (speedMode === 'exact' && speedExact) {
      constraints.speed_knots = Number(speedExact);
    } else if (speedMode === 'range') {
      if (speedMin) constraints.speed_min_knots = Number(speedMin);
      if (speedMax) constraints.speed_max_knots = Number(speedMax);
    }

    if (!allFuelsSelected && !noFuelsSelected) {
      constraints.allowed_fuels = Array.from(selectedFuels);
    }

    if (weatherOverride) {
      if (windSpeed) constraints.wind_speed = Number(windSpeed);
      if (waveHeight) constraints.wave_height = Number(waveHeight);
      if (temperature) constraints.temperature = Number(temperature);
      if (currentSpeed) constraints.current_speed = Number(currentSpeed);
    }

    if (deadline) {
      constraints.delivery_deadline = new Date(deadline).toISOString();
    }

    const hasConstraints = Object.keys(constraints).length > 0;

    onRun({
      iterations,
      swarm_size: swarmSize,
      seed,
      constraints: hasConstraints ? constraints : null,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Vessel */}
        <Card title="Vessel" icon={<Ship size={18} />} subtitle="Pin an exact hull, narrow by class, or leave fully open.">
          <fieldset className="flex flex-col gap-3">
            <legend className="sr-only">Vessel constraint mode</legend>
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Vessel constraint mode">
              {(
                [
                  ['open', 'Let optimizer choose (explore all)'],
                  ['type', 'Narrow by vessel type'],
                  ['exact', 'Pin exact vessel'],
                ] as [VesselMode, string][]
              ).map(([mode, label]) => (
                <button
                  key={mode}
                  type="button"
                  role="radio"
                  aria-checked={vesselMode === mode}
                  onClick={() => setVesselMode(mode)}
                  className={`cursor-pointer rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-ring ${
                    vesselMode === mode
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-border bg-background text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {vesselMode === 'type' && (
              <div className="flex flex-col gap-1.5">
                <label htmlFor={vesselTypeSelectId} className={fieldLabelClass}>
                  Vessel type
                </label>
                <select
                  id={vesselTypeSelectId}
                  value={vesselType}
                  onChange={(e) => setVesselType(e.target.value as VesselType)}
                  className={selectClass}
                >
                  {VESSEL_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {VESSEL_TYPE_LABELS[t]}
                    </option>
                  ))}
                </select>
                <p className={helperClass}>
                  QPSO searches every fleet vessel of this class; it still picks which one.
                </p>
              </div>
            )}

            {vesselMode === 'exact' && (
              <div className="flex flex-col gap-1.5">
                <label htmlFor={vesselIdSelectId} className={fieldLabelClass}>
                  Vessel
                </label>
                <select
                  id={vesselIdSelectId}
                  value={vesselId}
                  onChange={(e) => setVesselId(e.target.value)}
                  className={selectClass}
                >
                  <option value="">Select a vessel…</option>
                  {FLEET_CATALOG.map((v) => (
                    <option key={v.vessel_id} value={v.vessel_id}>
                      {v.vessel_id} — {VESSEL_TYPE_LABELS[v.vessel_type]} ({v.capacity_tonnes.toLocaleString()} t)
                    </option>
                  ))}
                </select>
                <p className={helperClass}>Every candidate in this run will use exactly this hull.</p>
              </div>
            )}

            {vesselMode === 'open' && (
              <p className={helperClass}>No vessel constraint — QPSO searches the full fleet catalog.</p>
            )}
          </fieldset>
        </Card>

        {/* Route */}
        <Card
          title="Route"
          icon={<RouteIcon size={18} />}
          subtitle="Leave blank to explore major global trade routes."
        >
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <label htmlFor={originId} className={fieldLabelClass}>
                  Origin <span className="font-normal text-muted-foreground">(optional)</span>
                </label>
                <select
                  id={originId}
                  value={origin}
                  onChange={(e) => onOriginChange(e.target.value)}
                  className={selectClass}
                >
                  <option value="">Any origin</option>
                  {ports.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.name}, {p.country}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor={destinationId} className={fieldLabelClass}>
                  Destination <span className="font-normal text-muted-foreground">(optional)</span>
                </label>
                <select
                  id={destinationId}
                  value={destination}
                  onChange={(e) => onDestinationChange(e.target.value)}
                  className={selectClass}
                >
                  <option value="">Any destination</option>
                  {ports.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.name}, {p.country}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {portsError && <p className="text-xs text-destructive">{portsError}</p>}

            {!origin && !destination && (
              <p className={helperClass}>
                Leave blank to explore major global trade routes (the optimizer's default route universe).
              </p>
            )}

            {(origin || destination) && !(origin && destination) && (
              <p className={helperClass}>Pick both an origin and a destination to see real route options.</p>
            )}

            {origin && destination && (
              <div className="flex flex-col gap-2">
                {routeLoading && (
                  <p className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                    Computing real route options for {origin} → {destination}…
                  </p>
                )}
                {routeError && <p className="text-xs text-destructive">{routeError}</p>}
                {!routeLoading && !routeError && routeOptions.length > 0 && (
                  <div role="radiogroup" aria-label="Route option" className="flex flex-col gap-2">
                    {routeOptions.length > 1 && (
                      <button
                        type="button"
                        role="radio"
                        aria-checked={selectedRouteIdx === 'either'}
                        onClick={() => onSelectedRouteIdxChange('either')}
                        className={`cursor-pointer rounded-lg border px-3 py-2 text-left text-xs transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-ring ${
                          selectedRouteIdx === 'either'
                            ? 'border-primary bg-primary/10 text-foreground'
                            : 'border-border bg-background text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        <span className="font-semibold">Let optimizer choose between these</span>
                        <span className="block font-normal">QPSO evaluates both and picks per-candidate.</span>
                      </button>
                    )}
                    {routeOptions.map((opt, idx) => (
                      <button
                        key={`${opt.origin}-${opt.destination}-${idx}`}
                        type="button"
                        role="radio"
                        aria-checked={selectedRouteIdx === idx}
                        onClick={() => onSelectedRouteIdxChange(idx)}
                        className={`cursor-pointer rounded-lg border px-3 py-2 text-left text-xs transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-ring ${
                          selectedRouteIdx === idx
                            ? 'border-primary bg-primary/10 text-foreground'
                            : 'border-border bg-background text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        <span className="font-semibold text-foreground">
                          Route {idx + 1}: via {opt.via}
                        </span>
                        <span className="block font-data font-normal">
                          {opt.distance_km.toLocaleString(undefined, { maximumFractionDigits: 0 })} km
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </Card>

        {/* Cargo */}
        <Card title="Cargo" icon={<Package size={18} />} subtitle="Optional — narrows nothing in the search, directly sets the assumed load.">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={cargoId} className={fieldLabelClass}>
              Cargo tonnage <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <input
              id={cargoId}
              type="number"
              min={0}
              step={100}
              placeholder="e.g. 25000"
              value={cargoTonnes}
              onChange={(e) => setCargoTonnes(e.target.value)}
              className={inputClass}
            />
            <p className={helperClass}>
              Leave blank to use a default utilization assumption for whichever vessel is chosen.
            </p>
          </div>
        </Card>

        {/* Speed */}
        <Card title="Speed" icon={<Gauge size={18} />} subtitle="Exact speed, a range, or let the optimizer decide (8-24 knots).">
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Speed constraint mode">
              {(
                [
                  ['open', `Let optimizer choose (${SPEED_MIN}-${SPEED_MAX} kn)`],
                  ['exact', 'Exact speed'],
                  ['range', 'Speed range'],
                ] as [SpeedMode, string][]
              ).map(([mode, label]) => (
                <button
                  key={mode}
                  type="button"
                  role="radio"
                  aria-checked={speedMode === mode}
                  onClick={() => setSpeedMode(mode)}
                  className={`cursor-pointer rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-ring ${
                    speedMode === mode
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-border bg-background text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {speedMode === 'exact' && (
              <div className="flex flex-col gap-1.5">
                <label htmlFor={speedExactId} className={fieldLabelClass}>
                  Speed (knots)
                </label>
                <input
                  id={speedExactId}
                  type="number"
                  min={0.1}
                  step={0.1}
                  placeholder="e.g. 18"
                  value={speedExact}
                  onChange={(e) => setSpeedExact(e.target.value)}
                  className={inputClass}
                />
              </div>
            )}

            {speedMode === 'range' && (
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1.5">
                  <label htmlFor={speedMinId} className={fieldLabelClass}>
                    Min (kn)
                  </label>
                  <input
                    id={speedMinId}
                    type="number"
                    min={0.1}
                    step={0.1}
                    placeholder={`${SPEED_MIN}`}
                    value={speedMin}
                    onChange={(e) => setSpeedMin(e.target.value)}
                    className={inputClass}
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label htmlFor={speedMaxId} className={fieldLabelClass}>
                    Max (kn)
                  </label>
                  <input
                    id={speedMaxId}
                    type="number"
                    min={0.1}
                    step={0.1}
                    placeholder={`${SPEED_MAX}`}
                    value={speedMax}
                    onChange={(e) => setSpeedMax(e.target.value)}
                    className={inputClass}
                  />
                </div>
              </div>
            )}
          </div>
        </Card>

        {/* Fuel */}
        <Card title="Fuel" icon={<FuelIcon size={18} />} subtitle="Defaults to all — narrow to the fuel types you want considered.">
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap gap-2">
              {FUEL_TYPES.map((fuel) => {
                const active = selectedFuels.has(fuel);
                return (
                  <button
                    key={fuel}
                    type="button"
                    aria-pressed={active}
                    onClick={() => toggleFuel(fuel)}
                    className={`cursor-pointer rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-ring ${
                      active
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border bg-background text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {FUEL_LABELS[fuel]}
                  </button>
                );
              })}
            </div>
            {noFuelsSelected && (
              <p className="text-xs text-destructive">
                Select at least one fuel type (or leave all selected to stay unconstrained).
              </p>
            )}
            {allFuelsSelected && <p className={helperClass}>All fuel types selected — unconstrained, optimizer chooses.</p>}
          </div>
        </Card>

        {/* Weather */}
        <Card
          title="Weather"
          icon={<CloudSun size={18} />}
          subtitle={
            origin && destination
              ? 'Live conditions sampled along the real route, from Open-Meteo.'
              : 'Select both origin and destination to see live weather along the route.'
          }
          defaultOpen={false}
          collapsible
        >
          <div className="flex flex-col gap-3">
            {!(origin && destination) && (
              <p className={helperClass}>
                Select both origin and destination in the Route section above to fetch live current
                conditions and a 5-day forecast sampled along that route. Until then, the optimizer falls
                back to a fixed "typical moderate conditions" placeholder for all four weather fields.
              </p>
            )}

            {origin && destination && weatherLoading && (
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                Fetching live weather along {origin} → {destination}… this calls Open-Meteo and can take a
                few seconds.
              </p>
            )}

            {origin && destination && !weatherLoading && weatherError && (
              <p className="flex items-center gap-2 text-xs text-destructive">
                <AlertTriangle size={12} aria-hidden="true" />
                {weatherError}
              </p>
            )}

            {origin && destination && !weatherLoading && !weatherError && weatherSummary && (
              <div className="flex flex-col gap-3 rounded-lg border border-border bg-background/50 p-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Current conditions along route
                  </p>
                  <p className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 font-data text-sm text-foreground">
                    <span className="flex items-center gap-1">
                      <Thermometer size={14} className="text-primary" aria-hidden="true" />
                      {weatherSummary.current.temperature.toFixed(1)}°C
                    </span>
                    <span className="flex items-center gap-1">
                      <Wind size={14} className="text-primary" aria-hidden="true" />
                      {weatherSummary.current.wind_speed.toFixed(1)} m/s wind
                    </span>
                    <span className="flex items-center gap-1">
                      <Waves size={14} className="text-primary" aria-hidden="true" />
                      {weatherSummary.current.wave_height.toFixed(1)} m waves
                    </span>
                  </p>
                  <p className={`${helperClass} mt-1`}>
                    Averaged across {weatherSummary.pointCount} waypoint{weatherSummary.pointCount === 1 ? '' : 's'} sampled
                    along the route.
                    {weatherSummary.anyPointErrored &&
                      ' Some waypoints could not be reached and are using documented fallback values.'}
                  </p>
                </div>

                {weatherSummary.forecast.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      5-day forecast (route average)
                    </p>
                    <div className="mt-1.5 grid grid-cols-5 gap-1.5">
                      {weatherSummary.forecast.map((day, idx) => (
                        <div
                          key={`${day.date}-${idx}`}
                          className="flex flex-col items-center gap-0.5 rounded-md border border-border bg-card px-1.5 py-1.5 text-center"
                        >
                          <span className="text-[10px] font-medium text-muted-foreground">{day.date}</span>
                          <span className="font-data text-xs font-semibold text-foreground">
                            {day.temp_max.toFixed(0)}° / {day.temp_min.toFixed(0)}°
                          </span>
                          <span className="flex items-center gap-0.5 font-data text-[10px] text-muted-foreground">
                            <Wind size={9} aria-hidden="true" />
                            {day.wind_speed_max.toFixed(1)}
                          </span>
                          <span className="flex items-center gap-0.5 font-data text-[10px] text-muted-foreground">
                            <Waves size={9} aria-hidden="true" />
                            {day.wave_height_max.toFixed(1)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-foreground">
              <input
                type="checkbox"
                checked={weatherOverride}
                onChange={(e) => setWeatherOverride(e.target.checked)}
                className="h-4 w-4 cursor-pointer rounded border-border accent-primary focus:outline-none focus:ring-2 focus:ring-ring"
              />
              Override weather manually
            </label>
            {!weatherOverride && (
              <p className={helperClass}>
                {origin && destination && weatherSummary
                  ? 'The optimizer will use live weather fetched fresh along the route at run time. Check the box above to pin exact values instead.'
                  : 'Uses a fixed "typical moderate conditions" placeholder when no route is set — the optimizer fetches live weather automatically once origin and destination are both given.'}
              </p>
            )}
            {weatherOverride && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                  <label htmlFor={windId} className={fieldLabelClass}>
                    Wind speed (m/s)
                  </label>
                  <input
                    id={windId}
                    type="number"
                    min={0}
                    step={0.1}
                    value={windSpeed}
                    onChange={(e) => setWindSpeed(e.target.value)}
                    className={inputClass}
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label htmlFor={waveId} className={fieldLabelClass}>
                    Wave height (m)
                  </label>
                  <input
                    id={waveId}
                    type="number"
                    min={0}
                    step={0.1}
                    value={waveHeight}
                    onChange={(e) => setWaveHeight(e.target.value)}
                    className={inputClass}
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label htmlFor={tempId} className={fieldLabelClass}>
                    Temperature (°C)
                  </label>
                  <input
                    id={tempId}
                    type="number"
                    step={0.1}
                    value={temperature}
                    onChange={(e) => setTemperature(e.target.value)}
                    className={inputClass}
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label htmlFor={currentId} className={fieldLabelClass}>
                    Current speed (kn, signed)
                  </label>
                  <input
                    id={currentId}
                    type="number"
                    step={0.1}
                    value={currentSpeed}
                    onChange={(e) => setCurrentSpeed(e.target.value)}
                    className={inputClass}
                  />
                </div>
              </div>
            )}
            {weatherOverride && weatherSummary && (
              <p className={helperClass}>
                Prefilled from the live route average above — edit any field to pin an exact value instead.
              </p>
            )}
          </div>
        </Card>

        {/* Deadline */}
        <Card
          title="Delivery Deadline"
          icon={<CalendarClock size={18} />}
          subtitle="Optional operational constraint."
          defaultOpen={false}
          collapsible
        >
          <div className="flex flex-col gap-1.5">
            <label htmlFor={deadlineId} className={fieldLabelClass}>
              Deadline <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <input
              id={deadlineId}
              type="datetime-local"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
              className={inputClass}
            />
            <p className={helperClass}>
              If a candidate's predicted arrival would miss this deadline, its reliability score is penalized.
              Leave blank to skip schedule-risk scoring entirely.
            </p>
          </div>
        </Card>
      </div>

      {/* Advanced (QPSO algorithm knobs) */}
      <Card
        title="Advanced: Algorithm Parameters"
        icon={<Settings2 size={18} />}
        subtitle="QPSO's own search knobs — implementation detail, not a voyage input."
        defaultOpen={false}
        collapsible
      >
        <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap">
          <div className="flex min-w-[140px] flex-1 flex-col gap-1.5">
            <label htmlFor={iterationsId} className={fieldLabelClass}>
              Iterations
            </label>
            <input
              id={iterationsId}
              type="number"
              min={1}
              max={500}
              value={iterations}
              onChange={(e) => setIterations(Number(e.target.value))}
              className={inputClass}
            />
          </div>
          <div className="flex min-w-[140px] flex-1 flex-col gap-1.5">
            <label htmlFor={swarmId} className={fieldLabelClass}>
              Swarm size
            </label>
            <input
              id={swarmId}
              type="number"
              min={1}
              max={200}
              value={swarmSize}
              onChange={(e) => setSwarmSize(Number(e.target.value))}
              className={inputClass}
            />
          </div>
          <div className="flex min-w-[140px] flex-1 flex-col gap-1.5">
            <label htmlFor={seedId} className={fieldLabelClass}>
              Seed <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <input
              id={seedId}
              type="number"
              placeholder="random"
              value={seed ?? ''}
              onChange={(e) => setSeed(e.target.value === '' ? null : Number(e.target.value))}
              className={inputClass}
            />
          </div>
        </div>
      </Card>

      {/* Summary + submit */}
      <div className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 shadow-sm sm:flex-row sm:items-center sm:justify-between sm:p-5">
        <p className="text-xs text-muted-foreground">
          <span className="font-semibold text-foreground">Constraints:</span> {constraintsSummary}
        </p>
        <button
          type="submit"
          disabled={isLoading || noFuelsSelected}
          className="flex cursor-pointer items-center justify-center gap-2 rounded-lg bg-primary px-6 py-2.5 font-semibold text-primary-foreground shadow-sm transition-all duration-200 hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-card disabled:cursor-not-allowed disabled:opacity-60 sm:min-w-[200px]"
        >
          {isLoading ? (
            <>
              <Loader2 size={18} className="animate-spin" aria-hidden="true" />
              Optimizing…
            </>
          ) : (
            <>
              <Play size={18} aria-hidden="true" />
              Run Optimization
            </>
          )}
        </button>
      </div>
    </form>
  );
}
