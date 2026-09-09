import { ArrowRight, Cloud, Database, GitBranch, Route as RouteIcon, ShieldCheck, Waypoints } from 'lucide-react';
import { Link } from 'react-router-dom';

const SERVICES = [
  {
    name: 'data-service',
    role: 'Ingests, cleans, validates and feature-engineers vessel, route, weather, AIS and fuel data, and exposes a query API for the rest of the pipeline.',
  },
  {
    name: 'prediction-service',
    role: 'An ensemble XGBoost model that turns processed features into fuel consumption, operating cost and voyage-time predictions.',
  },
  {
    name: 'emissions-service',
    role: 'A lifecycle GHG calculator built on real IMO and EU-MRV emission factors, converting predicted fuel and fuel type into emissions.',
  },
  {
    name: 'optimization-service',
    role: 'Generates candidate fleet configurations with a quantum-inspired optimizer (QPSO), scores each by round-tripping through prediction and emissions, then ranks the population with NSGA-II into a Pareto front.',
  },
  {
    name: 'gateway',
    role: 'Aggregates all four services behind a single API surface that the dashboard talks to.',
  },
];

const TECH = [
  {
    icon: GitBranch,
    title: 'XGBoost fuel prediction',
    body: 'Fuel consumption is driven by nonlinear interactions — speed-cubed drag, cargo load crossed with weather severity, vessel-type efficiency curves — that gradient-boosted trees capture natively, alongside the mixed numeric and categorical feature set the pipeline produces.',
  },
  {
    icon: ShieldCheck,
    title: 'Real IMO / EU-MRV emission factors',
    body: 'Lifecycle GHG figures are computed from the same emission-factor references used in regulatory reporting, not an approximated or made-up conversion.',
  },
  {
    icon: Waypoints,
    title: 'QPSO + NSGA-II optimization',
    body: 'Quantum-inspired particle swarm optimization explores the vessel/route/speed/fuel search space; NSGA-II performs non-dominated sorting on the scored population so the result is a genuine Pareto front, not a single "best" answer that hides trade-offs.',
  },
  {
    icon: RouteIcon,
    title: 'Real maritime routing',
    body: 'Route distances and waypoints come from searoute, reflecting actual sea corridors (Suez, Cape of Good Hope, and other real alternatives) instead of great-circle straight lines.',
  },
  {
    icon: Cloud,
    title: 'Live weather via Open-Meteo',
    body: 'Wind, wave and current data are sampled from Open-Meteo along the real computed route path and factored into fuel and voyage-time predictions.',
  },
  {
    icon: Database,
    title: 'Calibrated data foundation',
    body: 'Vessel specifications anchor on a real, public fleet dataset; a physics-plausible generator fills in what no free dataset provides jointly, with the full methodology documented in the data-service README.',
  },
];

export default function About() {
  return (
    <div>
      <section className="border-b border-border bg-card">
        <div className="mx-auto max-w-[900px] px-4 py-16 sm:px-6 sm:py-20">
          <p className="mb-4 inline-flex items-center rounded-full border border-border bg-muted px-3 py-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            About Q-Fleet AI
          </p>
          <h1 className="text-3xl font-extrabold tracking-tight text-foreground sm:text-4xl">
            A decision-support platform for maritime fleet operators
          </h1>
          <p className="mt-6 text-base leading-relaxed text-muted-foreground sm:text-lg">
            Q-Fleet AI predicts a vessel&rsquo;s fuel consumption, operating cost and voyage time
            with an ensemble machine-learning model, converts that into lifecycle greenhouse-gas
            emissions, then searches the space of fleet configurations — vessel, route, speed,
            fuel — with a quantum-inspired optimizer whose candidates are scored by calling back
            into the prediction and emissions logic. A ranking step turns the scored population
            into a Pareto front, which the dashboard presents to an operator for a deployment
            decision.
          </p>
        </div>
      </section>

      {/* Architecture */}
      <section className="mx-auto max-w-[900px] px-4 py-16 sm:px-6 sm:py-20">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-accent">Architecture</h2>
        <p className="mt-3 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
          Five microservices, one data contract.
        </p>
        <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
          Each service communicates over RabbitMQ — RPC request/reply for calls needing a
          synchronous answer, plain queues for fire-and-forget jobs — and every hand-off point
          in the pipeline is a versioned schema shared by every service rather than redefined
          per-service. The optimization loop is not a one-way pipeline: every candidate the
          optimizer proposes round-trips through prediction and emissions before the ranking
          step can run.
        </p>

        <ol className="mt-8 flex flex-col gap-4">
          {SERVICES.map((svc, idx) => (
            <li key={svc.name} className="flex gap-4 rounded-xl border border-border bg-card p-5 shadow-sm">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted font-data text-sm font-semibold text-foreground">
                {idx + 1}
              </span>
              <div>
                <p className="font-data text-sm font-semibold text-foreground">{svc.name}</p>
                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{svc.role}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* Technology */}
      <section className="border-t border-border bg-card">
        <div className="mx-auto max-w-[1400px] px-4 py-16 sm:px-6 sm:py-20">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-accent">The technology</h2>
          <p className="mt-3 max-w-2xl text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            Built on real models and real data, not approximations.
          </p>

          <div className="mt-10 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {TECH.map(({ icon: Icon, title, body }) => (
              <div key={title} className="rounded-xl border border-border bg-background p-6 shadow-sm">
                <span className="mb-4 flex h-11 w-11 items-center justify-center rounded-lg bg-muted text-accent">
                  <Icon size={20} aria-hidden="true" />
                </span>
                <h3 className="text-base font-semibold text-foreground">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="mx-auto flex max-w-[900px] flex-col items-start gap-4 px-4 py-16 sm:px-6 sm:py-20">
        <h2 className="text-2xl font-bold tracking-tight text-foreground">
          See it evaluate a real voyage.
        </h2>
        <Link
          to="/voyage-setup"
          className="flex cursor-pointer items-center gap-2 rounded-lg bg-accent px-6 py-3 font-semibold text-white shadow-sm transition-all duration-200 hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
        >
          Go to Voyage Setup
          <ArrowRight size={18} aria-hidden="true" />
        </Link>
      </section>
    </div>
  );
}
