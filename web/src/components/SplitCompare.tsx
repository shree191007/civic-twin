/** Two synced maps on one clock: the demo's proof moment. */
import { useState } from "react";
import { DeckMap } from "../map/DeckMap";
import { initialViewState, type ViewStateLike } from "../map/camera";
import { useScenario } from "../lib/useScenario";
import { people } from "../lib/format";
import type { Criticality, PlanId, TownshipResponse } from "../api/types";

interface Props {
  township: TownshipResponse;
  criticality: Criticality[];
  left: PlanId;
  right: PlanId;
}

export function SplitCompare({ township, criticality, left, right }: Props) {
  const [view, setView] = useState<ViewStateLike>(() => initialViewState(township.bbox));
  const a = useScenario(left);
  const b = useScenario(right);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, height: "100%" }}>
      {[
        { plan: left, state: a },
        { plan: right, state: b },
      ].map(({ plan, state }) => (
        <div key={plan} style={{ position: "relative", overflow: "hidden", borderRadius: 3 }}>
          <DeckMap
            township={township}
            frame={state.frame}
            criticality={criticality}
            sharedViewState={view}
            onViewStateChange={setView}
          />
          <div
            className="panel mono"
            style={{ position: "absolute", top: 8, left: 8, padding: "6px 9px", fontSize: 11 }}
          >
            <div style={{ textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {plan.replace(/_/g, " ")}
            </div>
            <div className="row" style={{ gap: 12, marginTop: 4 }}>
              <Counter label="no power" value={state.frame.totals.people_no_power} />
              <Counter label="no water" value={state.frame.totals.people_no_water} />
              <Counter label="no hospital" value={state.frame.totals.people_no_health} />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <div className="col" style={{ gap: 0 }}>
      <span style={{ fontSize: 13, color: value > 0 ? "var(--f-critical)" : "var(--text-dim)" }}>
        {people(value)}
      </span>
      <span className="dim" style={{ fontSize: 9 }}>{label}</span>
    </div>
  );
}
