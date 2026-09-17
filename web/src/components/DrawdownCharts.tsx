/** Small multiples: service level against hours, one per portfolio. */
import * as Plot from "@observablehq/plot";
import { useMemo } from "react";
import type { Frame } from "../lib/frames";
import { PlotFigure } from "./Plot";
import { css, PORTFOLIO_COLOR } from "../lib/colors";
import { hours } from "../lib/format";

const SERVICES: [string, string][] = [
  ["energy", "energy"],
  ["water", "water"],
  ["comms", "comms"],
  ["health", "services"],
  ["mobility", "transport"],
];

const RECOVERY = 0.9;

interface Props {
  timeline: Frame[];
  baseline?: Frame[];
}

/** Population-weighted mean service level at each hour. */
function series(timeline: Frame[], service: string): { t: number; level: number }[] {
  return timeline.map((frame) => {
    const zones = Object.values(frame.zones);
    const level = zones.length
      ? zones.reduce((sum, z) => sum + (z[service] ?? 1), 0) / zones.length
      : 1;
    return { t: frame.t, level };
  });
}

function recoveryHour(points: { t: number; level: number }[]): number | null {
  let at: number | null = null;
  for (const p of points) {
    if (p.level >= RECOVERY) at ??= p.t;
    else at = null;
  }
  return at;
}

export function DrawdownCharts({ timeline, baseline }: Props) {
  const charts = useMemo(
    () =>
      SERVICES.map(([service, portfolio]) => {
        const current = series(timeline, service);
        const base = baseline ? series(baseline, service) : null;
        const recovered = recoveryHour(current);
        return { service, portfolio, current, base, recovered };
      }),
    [timeline, baseline],
  );

  if (timeline.length === 0) {
    return <div className="dim" style={{ fontSize: 13, padding: 10 }}>No timeline loaded.</div>;
  }

  return (
    <div className="panel" style={{ padding: 8 }}>
      <h3 style={{ marginBottom: 6 }}>Service drawdown · share of demand met</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8 }}>
        {charts.map((c) => (
          <div key={c.service}>
            <div className="row mono" style={{ justifyContent: "space-between", fontSize: 11 }}>
              <span style={{ color: css(PORTFOLIO_COLOR[c.portfolio]) }}>{c.service}</span>
              <span className="dim">recovery {hours(c.recovered ?? Infinity)}</span>
            </div>
            <PlotFigure
              height={92}
              options={{
                height: 92,
                marginLeft: 26,
                marginBottom: 18,
                marginTop: 4,
                x: { label: "hours", ticks: 4, labelAnchor: "right" },
                y: { domain: [0, 1], label: null, ticks: 3, grid: true },
                marks: [
                  ...(c.base
                    ? [Plot.line(c.base, { x: "t", y: "level", stroke: "#94a3b8", strokeWidth: 1.2 })]
                    : []),
                  Plot.line(c.current, {
                    x: "t",
                    y: "level",
                    stroke: css(PORTFOLIO_COLOR[c.portfolio]),
                    strokeWidth: 1.6,
                  }),
                  ...(c.recovered != null
                    ? [
                        Plot.dot([{ t: c.recovered, level: RECOVERY }], {
                          x: "t",
                          y: "level",
                          fill: "#16a34a",
                          r: 2.5,
                        }),
                      ]
                    : []),
                ],
              }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
