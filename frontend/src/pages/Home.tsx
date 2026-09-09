import { ArrowRight, BarChart3, Cloud, Compass, Gauge, Leaf, Route as RouteIcon } from 'lucide-react';
import { Link } from 'react-router-dom';

const CAPABILITIES = [
  {
    icon: Gauge,
    title: 'Predict fuel, cost & time',
    body: 'An ensemble XGBoost model estimates fuel consumption, operating cost, and voyage time for any vessel, route, speed and fuel combination before a single decision is made.',
  },
  {
    icon: Leaf,
    title: 'Convert to lifecycle emissions',
    body: 'Predicted fuel is passed through a lifecycle GHG calculator built on real IMO and EU-MRV emission factors, giving each candidate a defensible carbon figure.',
  },
  {
    icon: Compass,
    title: 'Search the configuration space',
    body: 'A quantum-inspired optimizer (QPSO) generates candidate fleet configurations — vessel, route, speed, fuel — and scores each one by calling back into the prediction and emissions models.',
  },
  {
    icon: BarChart3,
    title: 'Rank with a Pareto front',
    body: 'NSGA-II ranks the scored population into a non-dominated Pareto front, so trade-offs between fuel, cost, emissions and reliability stay visible instead of collapsing to one number.',
  },
  {
    icon: RouteIcon,
    title: 'Real-world routing',
    body: 'Distances and waypoints come from actual maritime routing (searoute), not straight-line approximations — Suez, Cape and other real corridor options are represented.',
  },
  {
    icon: Cloud,
    title: 'Live weather along the route',
    body: 'Wind, wave and current conditions are sampled from Open-Meteo along the real route path, feeding directly into the fuel and voyage-time predictions.',
  },
];

export default function Home() {
  return (
    <div>
      {/* Hero */}
      <section className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-8 px-4 py-16 sm:px-6 sm:py-20 lg:flex-row lg:items-center lg:gap-16 lg:py-28">
          <div className="flex-1">
            <p className="mb-4 inline-flex items-center rounded-full border border-border bg-muted px-3 py-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Maritime Fleet Decision Support
            </p>
            <h1 className="text-4xl font-extrabold leading-[1.1] tracking-tight text-foreground sm:text-5xl lg:text-6xl">
              Optimize every voyage before you commit to it.
            </h1>
            <p className="mt-6 max-w-xl text-base leading-relaxed text-muted-foreground sm:text-lg">
              Q-Fleet AI predicts fuel consumption, operating cost and lifecycle emissions for
              any vessel-route-speed-fuel combination, then searches the full configuration
              space with a multi-objective optimizer to surface the trade-offs that matter for
              a deployment decision.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <Link
                to="/voyage-setup"
                className="flex cursor-pointer items-center justify-center gap-2 rounded-lg bg-accent px-6 py-3 font-semibold text-white shadow-sm transition-all duration-200 hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-card"
              >
                Start a Voyage Optimization
                <ArrowRight size={18} aria-hidden="true" />
              </Link>
              <Link
                to="/about"
                className="flex cursor-pointer items-center justify-center gap-2 rounded-lg border-2 border-primary px-6 py-3 font-semibold text-primary transition-all duration-200 hover:bg-primary hover:text-primary-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-card"
              >
                How it works
              </Link>
            </div>
          </div>

          <div className="flex-1">
            <div className="rounded-2xl border border-border bg-background p-6 shadow-lg sm:p-8">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                What a run returns
              </p>
              <dl className="mt-4 grid grid-cols-2 gap-4">
                {[
                  ['6', 'objectives scored per candidate'],
                  ['5', 'coordinated microservices'],
                  ['2', 'algorithms — QPSO + NSGA-II'],
                  ['0', 'straight-line route approximations'],
                ].map(([stat, label]) => (
                  <div key={label} className="rounded-lg bg-card p-4 shadow-sm">
                    <dt className="font-data text-3xl font-bold text-accent">{stat}</dt>
                    <dd className="mt-1 text-xs text-muted-foreground">{label}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>
        </div>
      </section>

      {/* What it does */}
      <section className="mx-auto max-w-[1400px] px-4 py-16 sm:px-6 sm:py-20">
        <div className="mb-12 max-w-2xl">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-accent">How it works</h2>
          <p className="mt-3 text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
            From raw voyage inputs to a ranked set of trade-offs.
          </p>
          <p className="mt-4 text-base leading-relaxed text-muted-foreground">
            Every optimization run moves through the same pipeline: predict, price the
            emissions, search the configuration space, and rank what comes back — so an
            operator sees the full trade-off, not a single black-box recommendation.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {CAPABILITIES.map(({ icon: Icon, title, body }) => (
            <div
              key={title}
              className="rounded-xl border border-border bg-card p-6 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md"
            >
              <span className="mb-4 flex h-11 w-11 items-center justify-center rounded-lg bg-muted text-accent">
                <Icon size={20} aria-hidden="true" />
              </span>
              <h3 className="text-base font-semibold text-foreground">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA band */}
      <section className="border-t border-border bg-primary">
        <div className="mx-auto flex max-w-[1400px] flex-col items-start gap-6 px-4 py-14 sm:px-6 sm:py-16 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-2xl font-bold text-primary-foreground sm:text-3xl">
              Ready to compare fleet configurations?
            </h2>
            <p className="mt-2 max-w-lg text-sm text-primary-foreground/80">
              Set your vessel, route and constraints once — Q-Fleet AI returns a Pareto front of
              non-dominated trade-offs in seconds.
            </p>
          </div>
          <Link
            to="/voyage-setup"
            className="flex shrink-0 cursor-pointer items-center justify-center gap-2 rounded-lg bg-white px-6 py-3 font-semibold text-primary shadow-sm transition-all duration-200 hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-white focus:ring-offset-2 focus:ring-offset-primary"
          >
            Go to Voyage Setup
            <ArrowRight size={18} aria-hidden="true" />
          </Link>
        </div>
      </section>
    </div>
  );
}
