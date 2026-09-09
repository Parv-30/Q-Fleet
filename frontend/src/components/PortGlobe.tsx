import { useEffect, useMemo, useRef, useState } from 'react';
import Globe, { type GlobeMethods } from 'react-globe.gl';
import { getPorts, ApiError } from '../api';
import type { Port, RouteOption } from '../types';

// Bundled by three-globe (react-globe.gl's rendering dependency) and
// re-exposed on unpkg for direct <img>/texture use -- the standard way
// react-globe.gl examples reference an earth texture without shipping their
// own image asset. Blue Marble reads as a clean, realistic, professional
// basemap (not cartoonish), so it's used for BOTH themes: per the task's
// fallback guidance, when a single texture is kept constant across themes
// the surrounding chrome (background/atmosphere/point colors) is what has
// to carry the light/dark distinction -- done below via explicit props.
const GLOBE_TEXTURE_URL = '//unpkg.com/three-globe/example/img/earth-blue-marble.jpg';
const GLOBE_BUMP_URL = '//unpkg.com/three-globe/example/img/earth-topology.png';

// Explicit light/dark chrome. react-globe.gl/three-globe render these as
// literal three.js material/scene colors, not CSS -- they do not inherit
// `.dark` class changes automatically (the same reason ParetoChart keeps two
// explicit Recharts palettes; see types.ts's FUEL_COLORS/FUEL_COLORS_DARK).
const THEME = {
  light: {
    backgroundColor: '#EFF3F8', // close to the light "muted" surface (#EEF2F6 family) in MASTER.md
    atmosphereColor: '#38BDF8',
    atmosphereAltitude: 0.22,
    unselectedPoint: '#64748B', // slate-500 -- reads on the light basemap without shouting
    originPoint: '#059669', // emerald-600 (distinct from destination + route colors)
    destinationPoint: '#DC2626', // red-600
    pathColor: '#0369A1', // light-mode accent, see MASTER.md
  },
  dark: {
    backgroundColor: '#0B1220', // --color-background, dark mode (MASTER.md)
    atmosphereColor: '#38BDF8', // --color-accent, dark mode
    atmosphereAltitude: 0.28,
    unselectedPoint: '#94A3B8', // --color-muted-foreground, dark mode
    originPoint: '#34D399', // FUEL_COLORS_DARK.hydrogen family (green, dark-safe)
    destinationPoint: '#F87171', // red-400, dark-safe
    pathColor: '#38BDF8', // --color-accent, dark mode
  },
} as const;

type PortRole = 'origin' | 'destination';

interface GlobePort extends Port {
  role: PortRole | null;
}

export interface PortGlobeProps {
  origin: string;
  destination: string;
  /** Fired when a marker is clicked on the globe, naming which field it should fill. */
  onSelectPort: (portName: string, role: PortRole) => void;
  /** Real route option currently shown in the form (if any) -- its `path` drives the drawn route shape. */
  selectedRoute?: RouteOption | null;
  isDark: boolean;
  className?: string;
}

const DEFAULT_ALTITUDE = 2.2;
const FLY_TO_ALTITUDE = 1.4;
const FLY_TO_MS = 1200;

export default function PortGlobe({
  origin,
  destination,
  onSelectPort,
  selectedRoute,
  isDark,
  className,
}: PortGlobeProps) {
  const globeRef = useRef<GlobeMethods | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [ports, setPorts] = useState<Port[]>([]);
  const [portsError, setPortsError] = useState('');
  const [dims, setDims] = useState({ width: 0, height: 480 });
  const [globeReady, setGlobeReady] = useState(false);
  const autoRotateRef = useRef(true);

  const theme = isDark ? THEME.dark : THEME.light;

  // Reuse the same port catalog fetch the dropdowns use -- no duplicate
  // fetch logic, just a second consumer of the existing getPorts() API fn.
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

  // Responsive sizing: react-globe.gl wants explicit width/height, not %.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setDims((prev) => ({ ...prev, width: entry.contentRect.width }));
    });
    observer.observe(el);
    setDims((prev) => ({ ...prev, width: el.clientWidth }));
    return () => observer.disconnect();
  }, []);

  // Auto-rotate when idle; stop on user drag/zoom, matching typical globe UX.
  useEffect(() => {
    if (!globeReady || !globeRef.current) return;
    const controls = globeRef.current.controls();
    controls.autoRotate = autoRotateRef.current;
    controls.autoRotateSpeed = 0.6;
    const stopRotation = () => {
      autoRotateRef.current = false;
      controls.autoRotate = false;
    };
    controls.addEventListener('start', stopRotation);
    return () => controls.removeEventListener('start', stopRotation);
  }, [globeReady]);

  const portsByName = useMemo(() => {
    const map = new Map<string, Port>();
    for (const p of ports) map.set(p.name, p);
    return map;
  }, [ports]);

  const pointsData: GlobePort[] = useMemo(
    () =>
      ports.map((p) => ({
        ...p,
        role: p.name === origin ? 'origin' : p.name === destination ? 'destination' : null,
      })),
    [ports, origin, destination]
  );

  // Fly the camera to a port when it becomes selected via the DROPDOWN (not
  // a globe click -- clicking a marker already puts that point front and
  // center under the cursor, so a second forced fly-to there would fight
  // the user's own camera move). Tracked via a ref pair so this effect can
  // tell "dropdown changed" apart from "globe click changed the same state".
  const lastFlownRef = useRef<{ origin: string; destination: string }>({ origin: '', destination: '' });
  const lastClickedRef = useRef<string | null>(null);

  useEffect(() => {
    if (!globeReady || !globeRef.current) return;
    const changedField: PortRole | null =
      origin !== lastFlownRef.current.origin
        ? 'origin'
        : destination !== lastFlownRef.current.destination
          ? 'destination'
          : null;
    lastFlownRef.current = { origin, destination };
    if (!changedField) return;

    const newValue = changedField === 'origin' ? origin : destination;
    if (!newValue || newValue === lastClickedRef.current) {
      // Either cleared, or this exact selection just came from a globe
      // click (already centered) -- skip the redundant fly-to.
      lastClickedRef.current = null;
      return;
    }
    const port = portsByName.get(newValue);
    if (!port) return;
    globeRef.current.pointOfView({ lat: port.lat, lng: port.lon, altitude: FLY_TO_ALTITUDE }, FLY_TO_MS);
  }, [origin, destination, portsByName, globeReady]);

  const handlePointClick = (point: object) => {
    const port = point as GlobePort;
    autoRotateRef.current = false;
    if (globeRef.current) {
      globeRef.current.controls().autoRotate = false;
    }
    lastClickedRef.current = port.name;
    // Cleanest single-callback API: caller decides which field to fill.
    // Default heuristic -- empty origin fills first, otherwise destination,
    // otherwise re-picking replaces whichever field this port already was,
    // otherwise overwrite destination (least surprising "start over" case).
    let role: PortRole;
    if (port.name === origin) role = 'origin';
    else if (port.name === destination) role = 'destination';
    else if (!origin) role = 'origin';
    else if (!destination) role = 'destination';
    else role = 'destination';
    onSelectPort(port.name, role);
  };

  // Real route path (Suez/Cape-shaped polyline) when the backend returned
  // one for the currently selected route option. Falls back to a straight
  // arc (arcsData) only when both ports are chosen but no real path is
  // available yet (e.g. route still loading) so the connection is still
  // visible immediately.
  const hasRealPath = !!selectedRoute?.path && selectedRoute.path.length >= 2;

  const pathsData = useMemo(() => {
    if (!hasRealPath) return [];
    return [{ path: selectedRoute!.path as [number, number][] }];
  }, [hasRealPath, selectedRoute]);

  const arcsData = useMemo(() => {
    if (hasRealPath) return [];
    const o = origin ? portsByName.get(origin) : undefined;
    const d = destination ? portsByName.get(destination) : undefined;
    if (!o || !d) return [];
    return [{ startLat: o.lat, startLng: o.lon, endLat: d.lat, endLng: d.lon }];
  }, [hasRealPath, origin, destination, portsByName]);

  return (
    <div ref={containerRef} className={className ?? ''}>
      {portsError && (
        <p role="alert" className="mb-2 text-xs text-destructive">
          {portsError}
        </p>
      )}
      {dims.width > 0 && (
        <Globe
          ref={globeRef}
          width={dims.width}
          height={dims.height}
          globeImageUrl={GLOBE_TEXTURE_URL}
          bumpImageUrl={GLOBE_BUMP_URL}
          backgroundColor={theme.backgroundColor}
          showAtmosphere
          atmosphereColor={theme.atmosphereColor}
          atmosphereAltitude={theme.atmosphereAltitude}
          onGlobeReady={() => {
            setGlobeReady(true);
            globeRef.current?.pointOfView({ lat: 20, lng: 40, altitude: DEFAULT_ALTITUDE }, 0);
          }}
          // Points (ports)
          pointsData={pointsData}
          pointLat="lat"
          pointLng="lon"
          pointAltitude={0.01}
          // Slightly larger than three-globe's own default (0.25) -- these
          // are real click targets (port selection), not decorative dots,
          // so they need a comfortably clickable radius, not just a visible
          // one.
          pointRadius={(d: object) => ((d as GlobePort).role ? 0.65 : 0.42)}
          pointColor={(d: object) => {
            const role = (d as GlobePort).role;
            if (role === 'origin') return theme.originPoint;
            if (role === 'destination') return theme.destinationPoint;
            return theme.unselectedPoint;
          }}
          pointLabel={(d: object) => {
            const p = d as GlobePort;
            const roleTag = p.role ? ` (${p.role})` : '';
            return `${p.name}, ${p.country}${roleTag}`;
          }}
          pointsMerge={false}
          onPointClick={handlePointClick}
          // Real route polyline when available
          pathsData={pathsData}
          pathPoints="path"
          pathPointLat={(p: unknown) => (p as [number, number])[0]}
          pathPointLng={(p: unknown) => (p as [number, number])[1]}
          pathColor={() => theme.pathColor}
          pathStroke={1.5}
          pathTransitionDuration={800}
          // Straight-arc fallback (no real path yet)
          arcsData={arcsData}
          arcColor={() => theme.pathColor}
          arcStroke={1}
          arcAltitudeAutoScale={0.3}
          arcDashLength={0.6}
          arcDashGap={0.3}
          arcDashAnimateTime={2500}
        />
      )}
    </div>
  );
}
