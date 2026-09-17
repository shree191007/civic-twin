/** Risk and criticality: loss distribution, contributions, ranking, SPOF cards. */
import * as Plot from "@observablehq/plot";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PlotFigure } from "../../components/Plot";
import { SpofCard } from "../../components/SpofCard";
import {
  useCriticality,
  useRedundancy,
  useRisk,
  useSpofs,
  useTownship,
} from "../../api/queries";
import { EstimateValue, UncertaintyDrivers } from "../../components/EstimateValue";
import { AssumptionLedger } from "../../components/AssumptionLedger";
import { RedundancyPanel } from "../../components/RedundancyPanel";
import { people, percent, personHours } from "../../lib/format";
import { css, PORTFOLIO_COLOR } from "../../lib/colors";
import { useStore } from "../../store";
import { frameTargets } from "../../map/camera";
import type { Criticality, Spof } from "../../api/types";

type SortKey = keyof Pick<
  Criticality,
  "asset_id" | "served_population" | "annual_failure_prob" | "tail_criticality_ph" | "systemic_ratio"
>;

const COLUMNS: [SortKey, string][] = [
  ["asset_id", "asset"],
  ["served_population", "people served"],
  ["annual_failure_prob", "P(fail)/yr"],
  ["tail_criticality_ph", "tail criticality"],
  ["systemic_ratio", "systemic ratio"],
];

/** The ratio divides by max(expected direct loss, 1), so an asset with almost
 *  no standalone loss shows an enormous ratio purely because the denominator
 *  was clamped. Those are shown as unmeasurable rather than as a huge number. */
function SystemicRatio({ row }: { row: Criticality }) {
  const clamped = (row.expected_direct_ph ?? 0) < 1;
  if (clamped) {
    return (
      <span
        className="dim"
        title={
          "Not meaningful: this asset has almost no standalone loss, so the " +
          "ratio's denominator was clamped to 1."
        }
      >
        n/a
      </span>
    );
  }
  return (
    <>
      {row.systemic_ratio.toFixed(2)}
      {row.systemic && (
        <span style={{ color: "var(--f-critical-text)", marginLeft: 5, fontSize: 11 }}>
          SYSTEMIC
        </span>
      )}
    </>
  );
}

/** Patch section 17: being central is not the same as mattering. */
function ImportanceBars({ structural, functional }: { structural: number; functional: number }) {
  return (
    <span className="row" style={{ gap: 3 }} title="structural / functional importance">
      <span style={{ width: 22, height: 5, background: "var(--border)", borderRadius: 1 }}>
        <span style={{ display: "block", width: `${structural * 100}%`, height: "100%", background: "var(--transport)", borderRadius: 1 }} />
      </span>
      <span style={{ width: 22, height: 5, background: "var(--border)", borderRadius: 1 }}>
        <span style={{ display: "block", width: `${functional * 100}%`, height: "100%", background: "var(--f-critical)", borderRadius: 1 }} />
      </span>
    </span>
  );
}

export function Risk() {
  const { data: risk } = useRisk();
  const { data: criticality } = useCriticality();
  const { data: spofs } = useSpofs();
  const { data: township } = useTownship();
  const { data: redundancy } = useRedundancy();
  const selectAsset = useStore((s) => s.selectAsset);
  const flyTo = useStore((s) => s.flyTo);
  const navigate = useNavigate();
  const [sort, setSort] = useState<SortKey>("tail_criticality_ph");

  const rows = useMemo(() => {
    const items = [...(criticality?.items ?? [])];
    items.sort((a, b) =>
      sort === "asset_id"
        ? a.asset_id.localeCompare(b.asset_id)
        : (b[sort] as number) - (a[sort] as number),
    );
    return items;
  }, [criticality, sort]);

  const varLine = (risk?.var95_ph ?? 0) / 1e6;
  const cvarLine = (risk?.cvar95_ph ?? 0) / 1e6;

  /** Binned here rather than by Plot, so the tail can be shaded separately. */
  const bins = useMemo(() => {
    const values = (risk?.loss_samples_ph ?? []).map((v) => v / 1e6);
    if (values.length === 0) return [];
    const max = Math.max(...values, varLine) * 1.02;
    const count = 34;
    const width = max / count;
    const counts = new Array(count).fill(0) as number[];
    for (const v of values) counts[Math.min(count - 1, Math.floor(v / width))] += 1;
    return counts.map((n, i) => ({
      x1: i * width,
      x2: (i + 1) * width,
      n,
      band: (i + 0.5) * width >= varLine ? "tail" : "body",
    }));
  }, [risk, varLine]);

  const contributions = useMemo(() => {
    const entries = Object.entries(risk?.service_contributions_ph ?? {});
    return entries.map(([service, value]) => ({ service, value }));
  }, [risk]);

  const positionOf = (assetId: string): [number, number] | null => {
    if (!township) return null;
    for (const collection of Object.values(township.layers)) {
      const hit = collection.features.find((f) => f.properties.id === assetId);
      if (hit) return hit.geometry.coordinates as [number, number];
    }
    return null;
  };

  const showAsset = (assetId: string) => {
    selectAsset(assetId);
    const point = positionOf(assetId);
    if (point) flyTo(frameTargets([point]) ?? { lon: point[0], lat: point[1], zoom: 14, pitch: 50 });
    navigate("/explore");
  };

  const showSpof = (spof: Spof) => {
    const ids = [spof.shared_asset, ...spof.redundant_group].filter(Boolean) as string[];
    const points = ids.map(positionOf).filter(Boolean) as [number, number][];
    if (spof.shared_asset) selectAsset(spof.shared_asset);
    const target = frameTargets(points);
    if (target) flyTo(target);
    navigate("/explore");
  };

  return (
    <div className="scroll" style={{ padding: 8, height: "100%" }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <div className="panel" style={{ padding: 10 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3>Annual loss distribution</h3>
            <span className="mono dim" style={{ fontSize: 12 }}>
              {risk ? `${risk.n_scenarios} scenario years` : ""}
            </span>
          </div>
          {risk && (
            <>
              <div className="row" style={{ gap: 22, margin: "8px 0" }}>
                <EstimateValue estimate={risk.eal} label="expected each year" />
                <EstimateValue estimate={risk.cvar95} label="in the worst years" />
                <EstimateValue
                  estimate={risk.people_affected}
                  label="people affected"
                  round
                />
              </div>
              <UncertaintyDrivers drivers={risk.cvar95?.drivers ?? []} />
            </>
          )}
          <PlotFigure
            height={200}
            empty="No loss table loaded."
            options={
              bins.length
                ? {
                    height: 200,
                    marginLeft: 40,
                    marginBottom: 30,
                    x: { label: "weighted loss (M person-hours)", grid: true },
                    y: { label: "scenario years" },
                    color: {
                      domain: ["body", "tail"],
                      range: ["#cbd5e1", "#dc2626"],
                      legend: false,
                    },
                    marks: [
                      Plot.rectY(bins, { x1: "x1", x2: "x2", y: "n", fill: "band" }),
                      Plot.ruleY([0], { stroke: "#94a3b8" }),
                      Plot.ruleX([varLine], { stroke: "#ca8a04", strokeWidth: 2 }),
                      Plot.ruleX([cvarLine], { stroke: "#dc2626", strokeWidth: 2 }),
                    ],
                  }
                : null
            }
          />
          {risk && (
            <div className="dim mono" style={{ fontSize: 11 }}>
              VaR95 (amber) and CVaR95 (red); the shaded tail is the worst{" "}
              {percent(1 - risk.alpha)} of years, averaging {personHours(risk.cvar95_ph)}.
            </div>
          )}
        </div>

        <div className="panel" style={{ padding: 10 }}>
          <h3>Where the tail risk sits</h3>
          <PlotFigure
            height={190}
            empty="No contributions."
            options={
              contributions.length
                ? {
                    height: 190,
                    marginLeft: 80,
                    x: { label: "CVaR contribution (M person-hours)", transform: (v: number) => v / 1e6, grid: true },
                    y: { label: null },
                    marks: [
                      Plot.barX(contributions, {
                        x: "value",
                        y: "service",
                        fill: (d: { service: string }) =>
                          css(PORTFOLIO_COLOR[d.service === "health" ? "services" : d.service] ?? PORTFOLIO_COLOR.transport),
                        sort: { y: "-x" },
                      }),
                    ],
                  }
                : null
            }
          />
          {risk && (
            <div className="dim mono" style={{ fontSize: 11 }}>
              These sum to CVaR95 = {personHours(risk.cvar95_ph)}. Worst zone: {risk.worst_zone}.
            </div>
          )}
        </div>
      </div>

      <div className="panel" style={{ padding: 10, marginTop: 8 }}>
        <h3>Asset criticality</h3>
        <div style={{ maxHeight: 300, overflow: "auto", marginTop: 6 }}>
          <table>
            <thead>
              <tr>
                {COLUMNS.map(([key, label]) => (
                  <th key={key} onClick={() => setSort(key)} aria-sort={sort === key ? "descending" : "none"}>
                    {label}
                    {sort === key ? " ▾" : ""}
                  </th>
                ))}
                <th title="structural importance (topology) vs functional importance (simulated loss)">
                  struct / funct
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 40).map((c) => (
                <tr key={c.asset_id} onClick={() => showAsset(c.asset_id)} style={{ cursor: "pointer" }}>
                  <td className="mono">{c.asset_id}</td>
                  <td className="mono">{people(c.served_population)}</td>
                  <td className="mono">{percent(c.annual_failure_prob, 1)}</td>
                  <td className="mono">{personHours(c.tail_criticality_ph)}</td>
                  <td className="mono">
                    <SystemicRatio row={c} />
                  </td>
                  <td>
                    <ImportanceBars
                      structural={c.structural_importance ?? 0}
                      functional={c.functional_importance ?? 0}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {risk && (
        <div style={{ marginTop: 8 }}>
          <AssumptionLedger ledger={risk.assumptions} provenance={risk.provenance} />
        </div>
      )}

      {redundancy && (
        <div style={{ marginTop: 8 }}>
          <RedundancyPanel
            groups={redundancy.groups}
            systemScore={redundancy.system_score}
          />
        </div>
      )}

      <h3 style={{ margin: "14px 0 6px" }}>
        Hidden single points of failure — what asset-by-asset monitoring misses
      </h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))", gap: 8 }}>
        {(spofs?.items ?? []).slice(0, 9).map((s, i) => (
          <SpofCard key={i} spof={s} onShow={showSpof} />
        ))}
      </div>
    </div>
  );
}
