// Natural Earth land polygons from the bundled `world-atlas` package (no network: Vite turns the
// JSON into a lazily loaded local chunk). Decoded once per resolution and cached.
import { feature } from 'topojson-client';
import type { GeometryCollection, Topology } from 'topojson-specification';
import type { GeoPermissibleObjects } from 'd3-geo';

export type LandDetail = '110m' | '50m';

const cache = new Map<LandDetail, Promise<GeoPermissibleObjects>>();

async function decode(detail: LandDetail): Promise<GeoPermissibleObjects> {
  const mod = detail === '50m' ? await import('world-atlas/land-50m.json') : await import('world-atlas/land-110m.json');
  const topo = (mod.default ?? mod) as unknown as Topology<{ land: GeometryCollection }>;
  return feature(topo, topo.objects.land) as GeoPermissibleObjects;
}

export function loadLand(detail: LandDetail): Promise<GeoPermissibleObjects> {
  let p = cache.get(detail);
  if (!p) {
    p = decode(detail);
    p.catch(() => cache.delete(detail)); // allow a retry after a failed chunk load
    cache.set(detail, p);
  }
  return p;
}
