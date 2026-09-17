/** Camera moves. flyTo frames an asset together with the zones it affects. */
import { FlyToInterpolator } from "@deck.gl/core";
import { easeCubicInOut } from "d3-ease";
import type { CameraTarget } from "../store";

export const DEFAULT_PITCH = 50;
export const FLY_MS = 1200;

export interface ViewStateLike {
  longitude: number;
  latitude: number;
  zoom: number;
  pitch: number;
  bearing: number;
  transitionDuration?: number;
  transitionInterpolator?: FlyToInterpolator;
  transitionEasing?: (t: number) => number;
}

export function initialViewState(
  bbox: [number, number, number, number],
): ViewStateLike {
  return {
    longitude: (bbox[0] + bbox[2]) / 2,
    latitude: (bbox[1] + bbox[3]) / 2,
    zoom: 12.2,
    pitch: DEFAULT_PITCH,
    bearing: -18,
  };
}

/** Zoom that fits a span of degrees into the viewport width. */
export function zoomForSpan(spanDeg: number, viewportPx = 900): number {
  const safe = Math.max(spanDeg, 1e-4);
  return Math.max(10, Math.min(16, Math.log2((360 * viewportPx) / (safe * 512 * 1.6))));
}

/** Frame an asset plus every point it affects. */
export function frameTargets(
  points: [number, number][],
  pitch = DEFAULT_PITCH,
): CameraTarget | null {
  if (points.length === 0) return null;
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  return {
    lon: (minLon + maxLon) / 2,
    lat: (minLat + maxLat) / 2,
    zoom: zoomForSpan(Math.max(maxLon - minLon, maxLat - minLat)),
    pitch,
  };
}

export function applyTarget(
  current: ViewStateLike,
  target: CameraTarget,
): ViewStateLike {
  return {
    ...current,
    longitude: target.lon,
    latitude: target.lat,
    zoom: target.zoom,
    pitch: target.pitch,
    bearing: target.bearing ?? current.bearing,
    transitionDuration: FLY_MS,
    transitionInterpolator: new FlyToInterpolator({ speed: 1.4 }),
    transitionEasing: easeCubicInOut,
  };
}

/** Pull back far enough to see all four portfolio planes at once. */
export function frameTheCascade(
  bbox: [number, number, number, number],
): CameraTarget {
  return {
    lon: (bbox[0] + bbox[2]) / 2,
    lat: (bbox[1] + bbox[3]) / 2 - 0.012,
    zoom: 11.4,
    pitch: 62,
    bearing: -22,
  };
}
