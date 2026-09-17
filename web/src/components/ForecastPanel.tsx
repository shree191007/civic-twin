/** Forecast to impact (patch sections 3, 4, 5 and 24).
 *
 *  Gotham does not forecast weather. This is where somebody else's forecast
 *  comes in, and what it does to the system comes out — as a range, because a
 *  forecast is a range.
 */
import { useState } from "react";
import * as Plot from "@observablehq/plot";
import { post } from "../api/client";
import type { HazardImpactResponse } from "../api/types";
import { EstimateValue, UncertaintyDrivers } from "./EstimateValue";
import { AssumptionLedger } from "./AssumptionLedger";
import { PlotFigure } from "./Plot";
import { people, personHours } from "../lib/format";
import { useStore } from "../store";

const EXAMPLES: { label: string; note: string; payload: string }[] = [
  {
    label: "Ensemble quantiles",
    note: "the shape most operational rainfall ensembles publish",
    payload: JSON.stringify(
      {
        id: "ensemble-72h",
        source: "regional ensemble",
        hazard_type: "river_flood",
        quantiles: { "0.1": 70, "0.5": 145, "0.9": 255 },
        start_time_h: 6,
        duration_h: 36,
      },
      null,
      2,
    ),
  },
  {
    label: "Single value",
    note: "widened into a band, and the widening declared as ours",
    payload: JSON.stringify(
      { id: "point-forecast", source: "deterministic run", depth_m: 1.3 },
      null,
      2,
    ),
  },
];

export function ForecastPanel() {
  const [text, setText] = useState(EXAMPLES[0].payload);
  const [result, setResult] = useState<HazardImpactResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const selectAsset = useStore((s) => s.selectAsset);

  const run = async () => {
    setPending(true);
    setError(null);
    try {
      const payload = JSON.parse(text);
      setResult(await post<HazardImpactResponse>("/hazard/impact", { ...payload, n_scenarios: 120 }));
    } catch (e) {
      setError(e instanceof SyntaxError ? "That is not valid JSON." : "The forecast was not accepted.");
      setResult(null);
    } finally {
      setPending(false);
    }
  };

  const bands = (result?.impact.bands ?? []).map((b) => ({
    band: `${Math.round(b.severity_low_mm)}–${Math.round(b.severity_high_mm)} mm`,
    probability: b.probability,
    central: b.person_hours.central / 1e6,
  }));

  return (
    <div className="col">
      <div className="panel" style={{ padding: 10 }}>
        <h3>Hazard in</h3>
        <div className="dim" style={{ fontSize: 12, margin: "4px 0 7px", lineHeight: 1.5 }}>
          Gotham does not forecast weather. Paste what an external model
          published and it answers what that hazard does to the system.
        </div>
        <div className="row" style={{ gap: 4, marginBottom: 6 }}>
          {EXAMPLES.map((e) => (
            <button
              key={e.label}
              style={{ fontSize: 12 }}
              title={e.note}
              onClick={() => setText(e.payload)}
            >
              {e.label}
            </button>
          ))}
        </div>
        <textarea
          rows={8}
          className="mono"
          style={{ fontSize: 12 }}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Hazard forecast payload"
        />
        <div className="row" style={{ marginTop: 6 }}>
          <button onClick={() => void run()} disabled={pending}>
            {pending ? "Simulating…" : "Simulate the cascade"}
          </button>
          {error && (
            <span className="mono" style={{ fontSize: 12, color: "var(--f-down-text)" }}>
              {error}
            </span>
          )}
        </div>
      </div>

      {result && (
        <>
          <div className="panel" style={{ padding: 10 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3>Impact out</h3>
              <span className="dim mono" style={{ fontSize: 12 }}>
                {result.impact.scenarios} scenarios · {result.forecast.source}
              </span>
            </div>
            <div className="row" style={{ gap: 22, margin: "8px 0" }}>
              <EstimateValue
                estimate={result.impact.people_affected}
                label="people affected"
                round
              />
              <EstimateValue
                estimate={result.impact.person_hours}
                label="service lost"
              />
            </div>
            <UncertaintyDrivers drivers={result.impact.primary_uncertainty} />
            {result.forecast.notes && (
              <div className="dim" style={{ fontSize: 12, marginTop: 6, lineHeight: 1.5 }}>
                {result.forecast.notes}
              </div>
            )}
            <h3 style={{ marginTop: 10 }}>By severity band</h3>
            <PlotFigure
              height={130}
              options={
                bands.length
                  ? {
                      height: 130,
                      marginLeft: 74,
                      x: { label: "service lost (M person-hours)", grid: true },
                      y: { label: null },
                      marks: [
                        Plot.barX(bands, {
                          x: "central",
                          y: "band",
                          fill: "#0284c7",
                          fillOpacity: (d: { probability: number }) =>
                            0.35 + 0.65 * d.probability,
                          sort: { y: "x" },
                        }),
                      ],
                    }
                  : null
              }
            />
            <div className="dim mono" style={{ fontSize: 11 }}>
              Bar opacity shows how likely the forecaster thinks each band is.
            </div>
            {result.impact.most_likely_first_failure.length > 0 && (
              <>
                <h3 style={{ marginTop: 10 }}>First to go</h3>
                <div className="row" style={{ gap: 4, flexWrap: "wrap" }}>
                  {result.impact.most_likely_first_failure.map((id) => (
                    <button
                      key={id}
                      className="mono"
                      style={{ fontSize: 12, padding: "1px 5px" }}
                      onClick={() => selectAsset(id)}
                    >
                      {id}
                    </button>
                  ))}
                </div>
              </>
            )}
            {result.impact.worst_zone && (
              <div className="dim" style={{ fontSize: 12, marginTop: 6 }}>
                Worst affected zone: {result.impact.worst_zone}. Tail case reaches{" "}
                {personHours(result.impact.tail_person_hours.central)}, with up to{" "}
                {people(result.impact.people_affected.high)} people affected.
              </div>
            )}
          </div>
          <AssumptionLedger ledger={result.assumptions} />
        </>
      )}
    </div>
  );
}
