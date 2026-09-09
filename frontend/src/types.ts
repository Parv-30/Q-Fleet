// Types matching the Q-Fleet AI gateway API surface.
// See /api/optimize (POST), /api/ports (GET), /api/routes (GET) and /health
// (GET) on the gateway service. Kept in lockstep with
// services/common/schemas.py (OptimizationConstraints, OptimizationCandidate)
// and data-service's app/routing.py (RouteOption) / app/port_catalog.py (Port).

export type FuelType =
  | 'hfo'
  | 'diesel'
  | 'lng'
  | 'methanol'
  | 'hydrogen'
  | 'ammonia';

export const FUEL_TYPES: FuelType[] = ['hfo', 'diesel', 'lng', 'methanol', 'hydrogen', 'ammonia'];

export const FUEL_LABELS: Record<FuelType, string> = {
  hfo: 'Heavy Fuel Oil (HFO)',
  diesel: 'Marine Diesel',
  lng: 'LNG',
  methanol: 'Methanol',
  hydrogen: 'Hydrogen',
  ammonia: 'Ammonia',
};

export type VesselType =
  | 'container'
  | 'bulk_carrier'
  | 'tanker'
  | 'ro_ro'
  | 'general_cargo';

export const VESSEL_TYPES: VesselType[] = [
  'container',
  'bulk_carrier',
  'tanker',
  'ro_ro',
  'general_cargo',
];

export const VESSEL_TYPE_LABELS: Record<VesselType, string> = {
  container: 'Container Ship',
  bulk_carrier: 'Bulk Carrier',
  tanker: 'Tanker',
  ro_ro: 'Ro-Ro',
  general_cargo: 'General Cargo',
};

// --- Fleet catalog (mirrors optimization-service/app/fleet_catalog.py) ----
// Hand-mirrored rather than fetched at runtime: there is no GET endpoint
// exposing FLEET_CATALOG today, and the task calls for offering these exact
// 10 vessels by name in a dropdown. If a /api/fleet endpoint is added later
// this can be swapped for a fetch with no consumer-facing change.
export interface FleetVessel {
  vessel_id: string;
  vessel_type: VesselType;
  capacity_tonnes: number;
}

export const FLEET_CATALOG: FleetVessel[] = [
  { vessel_id: 'CNT-COMPACT-01', vessel_type: 'container', capacity_tonnes: 40000 },
  { vessel_id: 'CNT-MEGA-01', vessel_type: 'container', capacity_tonnes: 200000 },
  { vessel_id: 'BULK-COMPACT-01', vessel_type: 'bulk_carrier', capacity_tonnes: 60000 },
  { vessel_id: 'BULK-MEGA-01', vessel_type: 'bulk_carrier', capacity_tonnes: 350000 },
  { vessel_id: 'TANK-COMPACT-01', vessel_type: 'tanker', capacity_tonnes: 60000 },
  { vessel_id: 'TANK-MEGA-01', vessel_type: 'tanker', capacity_tonnes: 280000 },
  { vessel_id: 'RORO-COMPACT-01', vessel_type: 'ro_ro', capacity_tonnes: 15000 },
  { vessel_id: 'RORO-STANDARD-01', vessel_type: 'ro_ro', capacity_tonnes: 55000 },
  { vessel_id: 'GC-COMPACT-01', vessel_type: 'general_cargo', capacity_tonnes: 8000 },
  { vessel_id: 'GC-STANDARD-01', vessel_type: 'general_cargo', capacity_tonnes: 30000 },
];

// --- /api/ports, /api/routes ------------------------------------------------

export interface Port {
  name: string;
  country: string;
  lat: number;
  lon: number;
}

export interface RouteOption {
  origin: string;
  destination: string;
  distance_km: number;
  via: string; // e.g. "Suez Canal, Bab-el-Mandeb Strait" or "Cape of Good Hope"
  // Real sea-lane path this route follows, as [lat, lon] points in travel
  // order (routing.py's RouteOption.path, a tuple of (lat, lon) tuples --
  // JSON-serializes as an array of 2-element arrays). Used to draw the
  // ACTUAL route shape on the globe (PortGlobe's pathsData layer) instead
  // of a straight great-circle arc. Optional/defaults to empty since older
  // hand-built fixtures may omit it.
  path?: [number, number][];
}

// --- Optimization constraints (mirrors common/schemas.py's OptimizationConstraints) ---
// Every field optional; a field left undefined/null means "fully open" for
// QPSO's search on that dimension. See schemas.py's docstring for the full
// hard-filter semantics (pin vs. narrow vs. open) this UI is built against.
export interface OptimizationConstraints {
  // Vessel
  vessel_id?: string | null;
  vessel_type?: VesselType | null;

  // Route
  origin?: string | null;
  destination?: string | null;
  route_id?: string | null;

  // Cargo
  cargo_tonnes?: number | null;

  // Speed
  speed_knots?: number | null;
  speed_min_knots?: number | null;
  speed_max_knots?: number | null;

  // Fuel
  allowed_fuels?: FuelType[] | null;

  // Weather (placeholder overrides -- see schemas.py's HONESTY NOTE)
  wind_speed?: number | null;
  wave_height?: number | null;
  temperature?: number | null;
  current_speed?: number | null;

  // Operational constraints (optional)
  delivery_deadline?: string | null; // ISO 8601 datetime
}

export interface OptimizationRequest {
  iterations?: number;
  swarm_size?: number;
  seed?: number | null;
  constraints?: OptimizationConstraints | null;
}

export interface PredictionValues {
  fuel_consumption: number;
  operating_cost: number;
  voyage_time: number;
}

export interface EmissionValues {
  lifecycle_ghg: number;
  [key: string]: number;
}

export interface ObjectiveValues {
  fuel_consumption: number;
  operating_cost: number;
  lifecycle_ghg: number;
  reliability: number;
  cargo_satisfaction: number;
  fleet_utilization: number;
}

export interface OptimizationCandidate {
  vessel: string;
  route: string;
  speed: number;
  fuel: FuelType;
  prediction: PredictionValues | null;
  emission: EmissionValues | null;
  objectives: ObjectiveValues;
}

export interface OptimizationResponse {
  pareto_front: OptimizationCandidate[];
}

export interface HealthResponse {
  status: string;
  services: {
    data: 'ok' | 'unreachable' | string;
    prediction: 'ok' | 'unreachable' | string;
    emissions: 'ok' | 'unreachable' | string;
    optimization: 'ok' | 'unreachable' | string;
  };
}

export type ObjectiveKey = keyof ObjectiveValues;

export interface ObjectiveMeta {
  label: string;
  unit: string;
  direction: 'minimize' | 'maximize';
}

export const OBJECTIVE_META: Record<ObjectiveKey, ObjectiveMeta> = {
  fuel_consumption: { label: 'Fuel Consumption', unit: 'tonnes', direction: 'minimize' },
  operating_cost: { label: 'Operating Cost', unit: 'USD', direction: 'minimize' },
  lifecycle_ghg: { label: 'Lifecycle GHG', unit: 'tonnes CO2e', direction: 'minimize' },
  reliability: { label: 'Reliability', unit: '0-1 index', direction: 'maximize' },
  cargo_satisfaction: { label: 'Cargo Satisfaction', unit: '0-1 index', direction: 'maximize' },
  fleet_utilization: { label: 'Fleet Utilization', unit: '0-1 index', direction: 'maximize' },
};

export const OBJECTIVE_KEYS: ObjectiveKey[] = [
  'fuel_consumption',
  'operating_cost',
  'lifecycle_ghg',
  'reliability',
  'cargo_satisfaction',
  'fleet_utilization',
];

// Recharts renders SVG fill/stroke as literal attribute values, not CSS --
// they will not pick up `.dark` class changes on their own. Two explicit
// palettes are kept so ParetoChart can select the active one from the
// isDark flag it receives as a prop. Each set is chosen to clear 4.5:1
// contrast against its own card background (#FFFFFF light / #111A2E dark;
// see design-system/q-fleet-ai/MASTER.md "Dark Mode Palette" for the
// contrast table this was checked against).
export const FUEL_COLORS: Record<FuelType, string> = {
  hfo: '#475569',
  diesel: '#1E40AF',
  lng: '#3B82F6',
  methanol: '#D97706',
  hydrogen: '#059669',
  ammonia: '#7C3AED',
};

export const FUEL_COLORS_DARK: Record<FuelType, string> = {
  hfo: '#94A3B8',
  diesel: '#60A5FA',
  lng: '#38BDF8',
  methanol: '#FBBF24',
  hydrogen: '#34D399',
  ammonia: '#A78BFA',
};
