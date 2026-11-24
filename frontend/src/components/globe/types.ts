// Public types of the <Globe> component.
import type { CityLight } from './cities';
import type { LandDetail } from './land';
import type { SkyKind } from './sky';

export interface GlobeMarker {
  id: string;
  lat: number;
  lon: number;
  color?: string;
  /** Dot radius in CSS pixels at zoom 1 (default 4). */
  size?: number;
  /** Short text drawn next to the marker (e.g. « 18° », « M5.2 »). */
  label?: string;
  /** Longer text shown on hover or when selected. */
  tooltip?: string;
  pulse?: boolean;
  /** Optional weather pictogram drawn in the label. */
  glyph?: SkyKind;
  /** Glyph variant for night (moon instead of sun). */
  night?: boolean;
}

/** Decorative orbit ring. Sizes are relative to the globe radius; tilt in degrees. */
export interface GlobeRing {
  radius: number;
  flatten: number;
  tilt: number;
  dots?: number;
  /** Angular speed of the dots, radians per second. */
  speed?: number;
  color?: string;
  width?: number;
  dashed?: boolean;
}

/** Rings, arcs and ticks drawn around the globe: faint at rest, lit and turning while `active`. */
export interface GlobeHud {
  active: boolean;
  /** Real level in [0, 1] read on every frame (e.g. the voice being heard or spoken): makes the rings pulse. */
  level?: () => number;
  /** Luminous spheres orbiting the globe on its rings (08/11/2026, owner's request): fast while AURORA thinks,
   *  slow and pulsing with `level` while it speaks, gone at rest. */
  spheres?: 'reflexion' | 'parole' | null;
}

export interface GlobeHandle {
  zoomBy: (factor: number) => void;
  resetView: () => void;
  focusOn: (lat: number, lon: number) => void;
  /** Rotate the view by a number of degrees (keyboard / buttons). */
  nudge: (dLon: number, dLat: number) => void;
}

/** Props read by the render engine on every frame. */
export interface GlobeRenderProps {
  detail?: LandDetail;
  autoRotate?: boolean;
  /** Degrees per second (default 4). */
  rotateSpeed?: number;
  interactive?: boolean;
  initialCenter?: { lat: number; lon: number };
  /** Initial zoom (default 1). */
  initialZoom?: number;
  minZoom?: number;
  maxZoom?: number;
  showNight?: boolean;
  /** Decorative lights drawn on the night side only. */
  cityLights?: readonly CityLight[] | null;
  graticule?: boolean;
  rings?: readonly GlobeRing[];
  hud?: GlobeHud | null;
  markers?: readonly GlobeMarker[];
  selectedId?: string | null;
  /** Instant used for the Sun position (default: now, refreshed every 30 s). */
  sunDate?: Date | null;
  onMarkerClick?: (marker: GlobeMarker) => void;
  /** Geographic position under the pointer, or (null, null) outside the globe. */
  onHover?: (lat: number | null, lon: number | null) => void;
  /** Called when the user starts dragging/zooming (lets a page pause its own auto-rotation). */
  onUserInteract?: () => void;
}
