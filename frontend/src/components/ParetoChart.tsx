import { useId, useMemo, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';
import {
  FUEL_COLORS,
  FUEL_COLORS_DARK,
  OBJECTIVE_KEYS,
  OBJECTIVE_META,
  type FuelType,
  type ObjectiveKey,
  type OptimizationCandidate,
} from '../types';

interface ParetoChartProps {
  candidates: OptimizationCandidate[];
  highlightedIndex?: number | null;
  // Recharts sets SVG fill/stroke as literal attributes, so they don't
  // inherit CSS custom property changes from the `.dark` class the way
  // border/text colors do -- the active fuel-color palette must be picked
  // explicitly based on the current theme.
  isDark?: boolean;
}

function axisLabel(key: ObjectiveKey): string {
  const meta = OBJECTIVE_META[key];
  const dir = meta.direction === 'minimize' ? 'minimize' : 'maximize';
  return `${meta.label} (${meta.unit}) — ${dir}`;
}

interface ChartPoint {
  x: number;
  y: number;
  candidate: OptimizationCandidate;
  originalIndex: number;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: { payload: ChartPoint }[];
}

function CustomTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  const c = point.candidate;

  return (
    <div className="max-w-xs rounded-lg border border-border bg-card p-3 text-xs shadow-lg">
      <p className="mb-1 font-semibold text-foreground">
        {c.vessel} <span className="font-normal text-muted-foreground">· {c.route}</span>
      </p>
      <p className="mb-2 font-data text-muted-foreground">
        {c.speed} kn · {c.fuel.toUpperCase()}
      </p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 font-data">
        {OBJECTIVE_KEYS.map((key) => (
          <div key={key} className="contents">
            <dt className="text-muted-foreground">{OBJECTIVE_META[key].label}</dt>
            <dd className="text-right text-foreground">
              {c.objectives[key].toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export default function ParetoChart({ candidates, highlightedIndex = null, isDark = false }: ParetoChartProps) {
  const xId = useId();
  const yId = useId();
  const [xKey, setXKey] = useState<ObjectiveKey>('fuel_consumption');
  const [yKey, setYKey] = useState<ObjectiveKey>('lifecycle_ghg');
  const activeFuelColors = isDark ? FUEL_COLORS_DARK : FUEL_COLORS;

  const fuelTypes = useMemo(() => {
    const set = new Set<FuelType>();
    candidates.forEach((c) => set.add(c.fuel));
    return Array.from(set);
  }, [candidates]);

  const seriesByFuel = useMemo(() => {
    return fuelTypes.map((fuel) => ({
      fuel,
      points: candidates
        .map((c, originalIndex) => ({ c, originalIndex }))
        .filter(({ c }) => c.fuel === fuel)
        .map<ChartPoint>(({ c, originalIndex }) => ({
          x: c.objectives[xKey],
          y: c.objectives[yKey],
          candidate: c,
          originalIndex,
        })),
    }));
  }, [candidates, fuelTypes, xKey, yKey]);

  return (
    <section
      aria-labelledby="pareto-chart-heading"
      className="rounded-xl border border-border bg-card p-4 shadow-sm sm:p-5"
    >
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 id="pareto-chart-heading" className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Pareto Front Comparison
        </h2>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="flex items-center gap-2">
            <label htmlFor={xId} className="text-xs font-medium text-muted-foreground">
              X axis
            </label>
            <select
              id={xId}
              value={xKey}
              onChange={(e) => setXKey(e.target.value as ObjectiveKey)}
              className="cursor-pointer rounded-lg border border-border bg-background px-2 py-1.5 text-sm text-foreground transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {OBJECTIVE_KEYS.map((key) => (
                <option key={key} value={key}>
                  {OBJECTIVE_META[key].label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor={yId} className="text-xs font-medium text-muted-foreground">
              Y axis
            </label>
            <select
              id={yId}
              value={yKey}
              onChange={(e) => setYKey(e.target.value as ObjectiveKey)}
              className="cursor-pointer rounded-lg border border-border bg-background px-2 py-1.5 text-sm text-foreground transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {OBJECTIVE_KEYS.map((key) => (
                <option key={key} value={key}>
                  {OBJECTIVE_META[key].label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="h-[420px] w-full sm:h-[480px]">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 10, right: 20, bottom: 56, left: 10 }}>
            <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
            <XAxis
              type="number"
              dataKey="x"
              name={OBJECTIVE_META[xKey].label}
              tick={{ fontSize: 11, fill: 'var(--color-muted-foreground)' }}
              stroke="var(--color-border)"
              label={{
                value: axisLabel(xKey),
                position: 'bottom',
                offset: 8,
                fontSize: 12,
                fill: 'var(--color-muted-foreground)',
              }}
            />
            <YAxis
              type="number"
              dataKey="y"
              name={OBJECTIVE_META[yKey].label}
              tick={{ fontSize: 11, fill: 'var(--color-muted-foreground)' }}
              stroke="var(--color-border)"
              label={{
                value: axisLabel(yKey),
                angle: -90,
                position: 'insideLeft',
                fontSize: 12,
                fill: 'var(--color-muted-foreground)',
              }}
            />
            <ZAxis range={[90, 90]} />
            <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3' }} />
            <Legend
              verticalAlign="bottom"
              align="center"
              iconSize={10}
              wrapperStyle={{
                fontSize: 12,
                paddingTop: 28,
                display: 'flex',
                flexWrap: 'wrap',
                justifyContent: 'center',
                columnGap: 16,
                rowGap: 4,
              }}
            />
            {seriesByFuel.map(({ fuel, points }) => (
              <Scatter
                key={fuel}
                name={fuel.toUpperCase()}
                data={points}
                fill={activeFuelColors[fuel]}
                shape={(props: unknown) => {
                  const { cx, cy, payload } = props as { cx: number; cy: number; payload: ChartPoint };
                  const isHighlighted = highlightedIndex === payload.originalIndex;
                  return (
                    <circle
                      cx={cx}
                      cy={cy}
                      r={isHighlighted ? 9 : 5}
                      fill={activeFuelColors[fuel]}
                      stroke={isHighlighted ? 'var(--color-accent)' : 'none'}
                      strokeWidth={isHighlighted ? 3 : 0}
                    />
                  );
                }}
              />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Hover a point to see all 6 objective values, vessel, route, speed and fuel type. No single
        candidate dominates all others on a Pareto front — compare trade-offs by switching axes.
      </p>
    </section>
  );
}
