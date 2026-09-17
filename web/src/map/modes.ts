/** Colour accessors per analysis mode. Every mode maps to a real metric. */
import type { AnalysisMode } from "../store";
import type { AssetProperties, Criticality } from "../api/types";
import {
  criticalityColor,
  floodColor,
  functionalityColor,
  FUNCTIONALITY_LEGEND,
  OPERATING_COLOR,
  OPERATING_LEGEND,
  PROVENANCE_COLOR,
  riskColor,
  type RGBA,
} from "../lib/colors";
import type { Frame } from "../lib/frames";

export interface ModeContext {
  frame: Frame;
  criticality: Map<string, Criticality>;
  maxCriticality: number;
  zoneCvar: Map<string, number>;
  maxZoneCvar: number;
}

export interface LegendEntry {
  label: string;
  color: RGBA;
}

export interface LegendSpec {
  title: string;
  units: string;
  entries: LegendEntry[];
  continuous?: boolean;
}

const DIM: RGBA = [70, 82, 98, 140];

export function assetColor(
  mode: AnalysisMode,
  asset: AssetProperties,
  ctx: ModeContext,
): RGBA {
  switch (mode) {
    case "functionality":
      return functionalityColor(ctx.frame.func[asset.id] ?? 1);
    case "state":
      return OPERATING_COLOR[ctx.frame.state[asset.id] ?? "operational"] ?? DIM;
    case "flood":
      return floodColor(ctx.frame.flood[asset.id] ?? 0);
    case "criticality": {
      const value = ctx.criticality.get(asset.id)?.tail_criticality_ph ?? 0;
      return criticalityColor(ctx.maxCriticality > 0 ? value / ctx.maxCriticality : 0);
    }
    case "provenance":
      return PROVENANCE_COLOR[asset.provenance] ?? DIM;
    case "risk":
      return DIM;
    default:
      return functionalityColor(1);
  }
}

export function zoneColor(mode: AnalysisMode, zoneId: string, ctx: ModeContext): RGBA {
  if (mode === "risk") {
    const value = ctx.zoneCvar.get(zoneId) ?? 0;
    return riskColor(ctx.maxZoneCvar > 0 ? value / ctx.maxZoneCvar : 0);
  }
  const services = ctx.frame.zones[zoneId];
  if (!services) return DIM;
  const worst = Math.min(
    services.energy ?? 1,
    services.water ?? 1,
    services.comms ?? 1,
    services.health ?? 1,
  );
  return functionalityColor(worst);
}

const ramp = (fn: (t: number) => RGBA, labels: string[]): LegendEntry[] =>
  labels.map((label, i) => ({ label, color: fn(i / (labels.length - 1)) }));

export function legendFor(mode: AnalysisMode, maxCriticality: number): LegendSpec {
  switch (mode) {
    case "functionality":
      return {
        title: "Functionality",
        units: "fraction of normal service",
        entries: FUNCTIONALITY_LEGEND,
      };
    case "state":
      return {
        title: "Operating state",
        units: "how the asset is running, not how damaged it is",
        entries: OPERATING_LEGEND,
      };
    case "flood":
      return {
        title: "Flood depth",
        units: "metres of standing water",
        entries: ramp((t) => floodColor(t * 3), ["0 m", "1 m", "2 m", "3 m+"]),
        continuous: true,
      };
    case "criticality": {
      const top = maxCriticality / 1e6;
      return {
        title: "Tail criticality",
        units: "person-hours of CVaR carried by the asset",
        entries: ramp(criticalityColor, [
          "0",
          `${(top / 3).toFixed(1)} M`,
          `${((2 * top) / 3).toFixed(1)} M`,
          `${top.toFixed(1)} M`,
        ]),
        continuous: true,
      };
    }
    case "provenance":
      return {
        title: "Provenance",
        units: "where the data came from",
        entries: [
          { label: "observed", color: PROVENANCE_COLOR.observed },
          { label: "inferred", color: PROVENANCE_COLOR.inferred },
          { label: "synthetic", color: PROVENANCE_COLOR.synthetic },
        ],
      };
    case "risk":
      return {
        title: "Zone tail risk",
        units: "person-hours of CVaR per zone (assets dimmed)",
        entries: ramp(riskColor, ["low", "", "", "high"]),
        continuous: true,
      };
  }
}

export const MODE_KEYS: Record<string, AnalysisMode> = {
  q: "functionality",
  a: "state",
  w: "flood",
  e: "criticality",
  r: "provenance",
  t: "risk",
};
