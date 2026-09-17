/** Efficient frontier: cumulative cost against CVaR, with a draggable budget. */
import * as Plot from "@observablehq/plot";
import { useMemo } from "react";
import type { PlanStep } from "../api/types";
import { PlotFigure } from "./Plot";
import { inr, personHours } from "../lib/format";

interface Props {
  steps: PlanStep[];
  budget: number;
  maxBudget: number;
  onBudget: (v: number) => void;
  cvarBefore: number;
}

export function FrontierChart({ steps, budget, maxBudget, onBudget, cvarBefore }: Props) {
  const points = useMemo(
    () => [
      { cost: 0, cvar: cvarBefore, label: "do nothing" },
      ...steps.map((s) => ({
        cost: s.cumulative_cost_inr,
        cvar: s.cvar_after,
        label: s.label || s.intervention_id,
      })),
    ],
    [steps, cvarBefore],
  );

  return (
    <div className="panel" style={{ padding: 10 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3>Efficient frontier</h3>
        <span className="mono dim" style={{ fontSize: 10 }}>
          budget {inr(budget)} · tail risk {personHours(
            points.filter((p) => p.cost <= budget).at(-1)?.cvar ?? cvarBefore,
          )}
        </span>
      </div>
      <PlotFigure
        height={190}
        empty="No frontier computed."
        options={
          points.length > 1
            ? {
                height: 190,
                marginLeft: 52,
                marginBottom: 30,
                x: { label: "cumulative cost (₹ crore)", transform: (v: number) => v / 1e7, grid: true },
                y: { label: "CVaR95 (M person-hours)", transform: (v: number) => v / 1e6, grid: true },
                marks: [
                  Plot.lineY(points, { x: "cost", y: "cvar", curve: "step-after", stroke: "#2ec8f0", strokeWidth: 1.6 }),
                  Plot.dot(points, { x: "cost", y: "cvar", fill: "#2ec8f0", r: 2.5, title: (d) => `${d.label}\n${inr(d.cost)}` }),
                  Plot.ruleX([budget], { stroke: "#f5c542", strokeWidth: 1.5 }),
                ],
              }
            : null
        }
      />
      <input
        type="range"
        min={0}
        max={maxBudget}
        step={100_000}
        value={budget}
        onChange={(e) => onBudget(Number(e.target.value))}
        aria-label="Resilience budget"
        style={{ width: "100%", marginTop: 4 }}
      />
    </div>
  );
}
