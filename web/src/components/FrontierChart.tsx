/** What each extra crore buys: tail risk remaining against money spent. */
import * as Plot from "@observablehq/plot";
import { useMemo } from "react";
import type { PlanStep } from "../api/types";
import { PlotFigure } from "./Plot";
import { inr } from "../lib/format";

interface Props {
  steps: PlanStep[];
  budget: number;
  cvarBefore: number;
  height?: number;
}

export function FrontierChart({ steps, budget, cvarBefore, height = 260 }: Props) {
  const points = useMemo(
    () => [
      { crore: 0, risk: cvarBefore / 1e6, label: "Nothing funded" },
      ...steps.map((s) => ({
        crore: s.cumulative_cost_inr / 1e7,
        risk: s.cvar_after / 1e6,
        label: s.label || s.intervention_id,
      })),
    ],
    [steps, cvarBefore],
  );

  if (points.length < 2) {
    return <div className="empty">No investment frontier has been computed yet.</div>;
  }

  const chosen = [...points].reverse().find((p) => p.crore * 1e7 <= budget) ?? points[0];
  const xMax = Math.max(points.at(-1)!.crore, budget / 1e7) * 1.05;
  const yMin = Math.min(...points.map((p) => p.risk));
  const yMax = points[0].risk;
  const pad = (yMax - yMin) * 0.15 || 0.1;

  return (
    <PlotFigure
      height={height}
      options={{
        height,
        marginLeft: 56,
        marginBottom: 40,
        marginRight: 16,
        style: { fontSize: "13px" },
        x: { label: "Money spent (₹ crore) →", domain: [0, xMax], grid: true },
        y: { label: "↑ Worst-year service loss (million person-hours)", domain: [yMin - pad, yMax + pad * 0.3], grid: true },
        marks: [
          Plot.areaY(points, {
            x: "crore",
            y1: yMin - pad,
            y2: "risk",
            curve: "step-after",
            fill: "#1d4ed8",
            fillOpacity: 0.07,
          }),
          Plot.lineY(points, { x: "crore", y: "risk", curve: "step-after", stroke: "#1d4ed8", strokeWidth: 2.2 }),
          Plot.dot(points, {
            x: "crore",
            y: "risk",
            r: 3,
            fill: "#fff",
            stroke: "#1d4ed8",
            strokeWidth: 1.5,
            title: (d: { label: string; crore: number }) => `${d.label}\n${inr(d.crore * 1e7)} spent in total`,
          }),
          Plot.ruleX([budget / 1e7], { stroke: "#ca8a04", strokeWidth: 2, strokeDasharray: "5,4" }),
          Plot.dot([chosen], { x: "crore", y: "risk", r: 7, fill: "#1d4ed8", stroke: "#fff", strokeWidth: 2 }),
          Plot.text([chosen], {
            x: "crore",
            y: "risk",
            text: () => "Your package",
            dy: -16,
            fontWeight: 700,
            fill: "#1d4ed8",
            fontSize: 13,
          }),
        ],
      }}
    />
  );
}
