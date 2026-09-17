/** Colour scales. Status is always functionality; portfolio colour never is. */

export type RGBA = [number, number, number, number];

export const PORTFOLIO_COLOR: Record<string, RGBA> = {
  energy: [217, 119, 6, 255],
  water: [2, 132, 199, 255],
  comms: [124, 58, 237, 255],
  transport: [100, 116, 139, 255],
  services: [225, 29, 72, 255],
};

export const PROVENANCE_COLOR: Record<string, RGBA> = {
  observed: [15, 23, 42, 255],
  inferred: [100, 116, 139, 255],
  synthetic: [163, 177, 194, 255],
};

const F_FULL: RGBA = [22, 163, 74, 255];
const F_DEGRADED: RGBA = [202, 138, 4, 255];
const F_CRITICAL: RGBA = [234, 88, 12, 255];
const F_DOWN: RGBA = [220, 38, 38, 255];

/** Four-stop functionality scale. Steps, never interpolated across a stop. */
export function functionalityColor(f: number): RGBA {
  if (!Number.isFinite(f)) return F_DOWN;
  if (f >= 0.95) return F_FULL;
  if (f >= 0.6) return F_DEGRADED;
  if (f > 0.0) return F_CRITICAL;
  return F_DOWN;
}

export const FUNCTIONALITY_LEGEND = [
  { label: "full (≥95%)", color: F_FULL },
  { label: "degraded (60–95%)", color: F_DEGRADED },
  { label: "critical (<60%)", color: F_CRITICAL },
  { label: "out of service", color: F_DOWN },
];

/** Operating states (patch section 9). Distinct from the functionality scale:
 *  an asset on backup is working, and that is worth seeing on its own. */
export const OPERATING_COLOR: Record<string, RGBA> = {
  operational: F_FULL,
  degraded: F_DEGRADED,
  backup: [2, 132, 199, 255],
  critical: F_CRITICAL,
  failed: F_DOWN,
};

export const OPERATING_LEGEND = [
  { label: "operational", color: OPERATING_COLOR.operational },
  { label: "degraded", color: OPERATING_COLOR.degraded },
  { label: "on backup", color: OPERATING_COLOR.backup },
  { label: "critical", color: OPERATING_COLOR.critical },
  { label: "failed", color: OPERATING_COLOR.failed },
];

function ramp(stops: RGBA[], t: number): RGBA {
  const x = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0));
  const span = (stops.length - 1) * x;
  const i = Math.min(stops.length - 2, Math.floor(span));
  const f = span - i;
  const a = stops[i];
  const b = stops[i + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
    255,
  ];
}

const FLOOD_STOPS: RGBA[] = [
  [224, 242, 254, 255],
  [125, 211, 252, 255],
  [2, 132, 199, 255],
  [12, 74, 110, 255],
];
const CRITICALITY_STOPS: RGBA[] = [
  [254, 243, 199, 255],
  [251, 191, 36, 255],
  [217, 119, 6, 255],
  [146, 64, 14, 255],
];
const RISK_STOPS: RGBA[] = [
  [254, 226, 226, 255],
  [248, 113, 113, 255],
  [220, 38, 38, 255],
  [127, 29, 29, 255],
];

/** Flood depth in metres, 0–3 m. */
export const floodColor = (depth_m: number): RGBA => ramp(FLOOD_STOPS, depth_m / 3);
/** Normalised tail criticality, 0–1. */
export const criticalityColor = (t: number): RGBA => ramp(CRITICALITY_STOPS, t);
/** Normalised zone CVaR, 0–1. */
export const riskColor = (t: number): RGBA => ramp(RISK_STOPS, t);

export function css(c: RGBA): string {
  return `rgb(${c[0]} ${c[1]} ${c[2]})`;
}

export function dim(c: RGBA, alpha: number): RGBA {
  return [c[0], c[1], c[2], Math.round(alpha * 255)];
}
