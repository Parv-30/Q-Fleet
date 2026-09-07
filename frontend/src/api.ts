import type { OptimizationRequest, OptimizationResponse, Port, RouteOption } from './types';

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) || 'http://localhost:8080';

/**
 * Sample response shape used only for local reference/testing.
 * Not used automatically — the app always calls the real gateway.
 */
export const MOCK_OPTIMIZATION_RESPONSE: OptimizationResponse = {
  pareto_front: [
    {
      vessel: 'MV Coral Explorer',
      route: 'Singapore-Rotterdam',
      speed: 18.5,
      fuel: 'lng',
      prediction: null,
      emission: null,
      objectives: {
        fuel_consumption: 210.4,
        operating_cost: 145000,
        lifecycle_ghg: 320.7,
        reliability: 0.91,
        cargo_satisfaction: 0.87,
        fleet_utilization: 0.78,
      },
    },
    {
      vessel: 'MV Coral Explorer',
      route: 'Singapore-Rotterdam',
      speed: 16.2,
      fuel: 'diesel',
      prediction: null,
      emission: null,
      objectives: {
        fuel_consumption: 185.2,
        operating_cost: 132500,
        lifecycle_ghg: 410.3,
        reliability: 0.88,
        cargo_satisfaction: 0.82,
        fleet_utilization: 0.74,
      },
    },
    {
      vessel: 'MV Pacific Voyager',
      route: 'Shanghai-Los Angeles',
      speed: 20.1,
      fuel: 'methanol',
      prediction: null,
      emission: null,
      objectives: {
        fuel_consumption: 245.9,
        operating_cost: 168300,
        lifecycle_ghg: 280.1,
        reliability: 0.94,
        cargo_satisfaction: 0.9,
        fleet_utilization: 0.85,
      },
    },
    {
      vessel: 'MV Northern Star',
      route: 'Hamburg-New York',
      speed: 14.8,
      fuel: 'hydrogen',
      prediction: null,
      emission: null,
      objectives: {
        fuel_consumption: 160.0,
        operating_cost: 178900,
        lifecycle_ghg: 95.4,
        reliability: 0.8,
        cargo_satisfaction: 0.75,
        fleet_utilization: 0.68,
      },
    },
  ],
};

export class ApiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function runOptimization(
  request: OptimizationRequest,
  signal?: AbortSignal
): Promise<OptimizationResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/optimize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err;
    }
    throw new ApiError(
      `Could not reach the Q-Fleet gateway at ${API_BASE_URL}. Is it running?`
    );
  }

  if (!response.ok) {
    let detail = '';
    try {
      const body = await response.json();
      detail = body?.detail ? `: ${body.detail}` : '';
    } catch {
      // ignore body parse errors
    }
    throw new ApiError(`Optimization request failed (HTTP ${response.status})${detail}`);
  }

  return (await response.json()) as OptimizationResponse;
}

export async function getPorts(signal?: AbortSignal): Promise<Port[]> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/ports`, { signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err;
    }
    throw new ApiError(`Could not reach the Q-Fleet gateway at ${API_BASE_URL}. Is it running?`);
  }

  if (!response.ok) {
    throw new ApiError(`Failed to load port catalog (HTTP ${response.status})`);
  }

  return (await response.json()) as Port[];
}

/**
 * Real route option(s) (Suez vs. Cape alternatives, when they meaningfully
 * differ) for a specific origin/destination pair, via data-service's
 * compute_route(). Returns 1-2 options -- see routing.py's docstring.
 */
export async function getRoutes(
  origin: string,
  destination: string,
  signal?: AbortSignal
): Promise<RouteOption[]> {
  let response: Response;
  try {
    const params = new URLSearchParams({ origin, destination });
    response = await fetch(`${API_BASE_URL}/api/routes?${params.toString()}`, { signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err;
    }
    throw new ApiError(`Could not reach the Q-Fleet gateway at ${API_BASE_URL}. Is it running?`);
  }

  if (!response.ok) {
    let detail = '';
    try {
      const body = await response.json();
      detail = body?.detail ? `: ${body.detail}` : '';
    } catch {
      // ignore body parse errors
    }
    throw new ApiError(`Failed to load route options (HTTP ${response.status})${detail}`);
  }

  return (await response.json()) as RouteOption[];
}
