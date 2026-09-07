# Q-Fleet AI Dashboard

A data-dense React dashboard for Q-Fleet AI's multi-objective voyage optimizer. Operators set
optimization parameters (iterations, swarm size, seed), run the QPSO+NSGA-II optimizer via the
gateway API, and explore the returned Pareto front of non-dominated vessel/route/speed/fuel
trade-offs across six objectives (fuel consumption, operating cost, lifecycle GHG, reliability,
cargo satisfaction, fleet utilization) through an interactive scatter chart and a sortable results
table.

## Running

```bash
npm install
npm run dev
```

The dev server starts on the default Vite port (usually `http://localhost:5173`).

## Configuration

The API base URL is configured via the `VITE_API_BASE_URL` environment variable (see
`.env.example`). Copy it to `.env.local` (already present for local dev) and point it at your
running gateway instance — defaults to `http://localhost:8080`. Changing this is a one-line edit
to repoint the dashboard once the gateway service is available.

## Build

```bash
npm run build
```
