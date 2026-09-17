/** Top-left heads-up display: versions, scenario, hour, and four live counters. */
import { people } from "../lib/format";
import { readout, type Frame } from "../lib/frames";
import type { TownshipResponse } from "../api/types";
import { isUsingFixtures } from "../api/client";

interface HudProps {
  township: TownshipResponse;
  frame: Frame;
  scenarioId: string;
  plan: string;
  kindOf: (assetId: string) => string | undefined;
}

const COUNTERS: [keyof Frame["totals"], string][] = [
  ["people_no_power", "no power"],
  ["people_no_water", "no water"],
  ["people_no_comms", "no comms"],
  ["people_no_health", "no hospital"],
];

export function Hud({ township, frame, scenarioId, plan, kindOf }: HudProps) {
  return (
    <div
      className="panel mono"
      style={{
        position: "absolute",
        top: 10,
        left: 10,
        padding: "8px 10px",
        fontSize: 11,
        maxWidth: 460,
        pointerEvents: "none",
      }}
    >
      <div className="row" style={{ gap: 10 }}>
        <strong style={{ fontSize: 12 }}>{township.name}</strong>
        <span className="dim">data {township.data_version}</span>
        {isUsingFixtures() && (
          <span style={{ color: "var(--f-degraded)" }}>fixture data</span>
        )}
      </div>
      <div className="row dim" style={{ gap: 10, marginTop: 2 }}>
        <span>{scenarioId}</span>
        <span>plan: {plan}</span>
        <span>H+{Math.round(frame.t)}</span>
      </div>
      <div className="row" style={{ gap: 14, marginTop: 7 }}>
        {COUNTERS.map(([key, label]) => (
          <div key={key} className="col" style={{ gap: 0 }}>
            <span style={{ fontSize: 14, color: frame.totals[key] > 0 ? "var(--f-critical)" : "var(--text-dim)" }}>
              {people(frame.totals[key])}
            </span>
            <span className="dim" style={{ fontSize: 9 }}>{label}</span>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-dim)" }}>
        {readout(frame, kindOf)}
      </div>
    </div>
  );
}
