/** The plan builder: frontier, chosen interventions, and the split comparison. */
import { useState } from "react";
import { FrontierChart } from "../../components/FrontierChart";
import { ConfidenceBadge } from "../../components/ConfidenceBadge";
import { SplitCompare } from "../../components/SplitCompare";
import { TimelineScrubber } from "../../components/TimelineScrubber";
import { DeckMap } from "../../map/DeckMap";
import {
  useBaselines,
  useCriticality,
  useFrontier,
  useObjectives,
  usePlans,
  useTownship,
} from "../../api/queries";
import { AssumptionLedger } from "../../components/AssumptionLedger";
import { useScenario } from "../../lib/useScenario";
import { useStore } from "../../store";
import { inr, personHours } from "../../lib/format";

export function Plan() {
  const { data: township } = useTownship();
  const { data: frontier } = useFrontier();
  const { data: criticality } = useCriticality();
  const { data: baselines } = useBaselines();
  const { data: objectives } = useObjectives();
  const objective = useStore((s) => s.objective);
  const setObjective = useStore((s) => s.setObjective);
  const budget = useStore((s) => s.budget);
  const setBudget = useStore((s) => s.setBudget);
  const { data: plans, isFetching } = usePlans(budget);
  const { frame } = useScenario();
  const [split, setSplit] = useState(true);

  if (!township) return <div className="dim" style={{ padding: 20 }}>Loading…</div>;

  const maxBudget = frontier?.max_budget_inr ?? 120_000_000;
  const items = criticality?.items ?? [];
  const targets = new Set((plans?.interventions ?? []).map((i) => i.target_asset));

  return (
    <div className="stack-narrow" style={{ display: "grid", gridTemplateColumns: "420px 1fr", gap: 8, padding: 8, height: "100%" }}>
      <div className="col scroll" style={{ minHeight: 0 }}>
        <div className="panel" style={{ padding: 10 }}>
          <h3>Priority</h3>
          <div className="dim" style={{ fontSize: 10, margin: "4px 0 7px", lineHeight: 1.5 }}>
            What the optimiser is asked to minimise. This changes what gets
            bought, not just how the result is described.
          </div>
          <div className="col" style={{ gap: 4 }}>
            {(objectives?.objectives ?? []).map((o) => (
              <label
                key={o.mode}
                className="row"
                style={{
                  gap: 7,
                  alignItems: "flex-start",
                  cursor: "pointer",
                  padding: "4px 6px",
                  borderRadius: 2,
                  background: objective === o.mode ? "var(--bg-elevated)" : "transparent",
                }}
              >
                <input
                  type="radio"
                  name="objective"
                  checked={objective === o.mode}
                  onChange={() => setObjective(o.mode)}
                  style={{ width: "auto", marginTop: 2 }}
                />
                <span className="col" style={{ gap: 1 }}>
                  <span style={{ fontSize: 12 }}>{o.label}</span>
                  <span className="dim" style={{ fontSize: 10, lineHeight: 1.4 }}>
                    {o.description}
                  </span>
                </span>
              </label>
            ))}
          </div>
          {plans?.plan.objective && plans.plan.objective.mode !== objective && (
            <div
              className="mono"
              style={{ fontSize: 10, marginTop: 7, color: "var(--f-degraded)" }}
            >
              The precomputed plan was optimised for
              {" "}
              {plans.plan.objective.label.toLowerCase()}. Re-run the analysis with
              {" "}
              <code>--objective {objective}</code> to plan for this priority.
            </div>
          )}
        </div>

        <FrontierChart
          steps={frontier?.steps ?? []}
          budget={budget}
          maxBudget={maxBudget}
          onBudget={setBudget}
          cvarBefore={plans?.plan.cvar_before ?? 0}
        />

        <div className="panel" style={{ padding: 10 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3>Selected package</h3>
            {isFetching && <span className="dim mono" style={{ fontSize: 9 }}>updating…</span>}
          </div>
          {plans && (
            <div className="row mono" style={{ gap: 16, margin: "6px 0 10px" }}>
              <div className="col" style={{ gap: 0 }}>
                <span style={{ fontSize: 14 }}>{inr(plans.plan.cost_inr)}</span>
                <span className="dim" style={{ fontSize: 9 }}>committed</span>
              </div>
              <div className="col" style={{ gap: 0 }}>
                <span style={{ fontSize: 14, color: "var(--f-full)" }}>
                  −{plans.plan.cvar_reduction_pct.toFixed(1)}%
                </span>
                <span className="dim" style={{ fontSize: 9 }}>tail risk</span>
              </div>
              <div className="col" style={{ gap: 0 }}>
                <span style={{ fontSize: 14 }}>{personHours(plans.plan.cvar_after)}</span>
                <span className="dim" style={{ fontSize: 9 }}>CVaR95 after</span>
              </div>
            </div>
          )}
          <div className="col" style={{ gap: 7 }}>
            {(plans?.interventions ?? []).map((item) => (
              <div key={item.id} style={{ borderTop: "1px solid var(--border)", paddingTop: 6 }}>
                <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
                  <span style={{ fontSize: 12 }}>{item.label}</span>
                  <ConfidenceBadge value={item.selection_frequency} />
                </div>
                <div className="row mono dim" style={{ fontSize: 10, gap: 10 }}>
                  <span>{inr(item.cost_inr)}</span>
                  <span>−{personHours(item.cvar_reduction_ph)}</span>
                </div>
                <div className="dim" style={{ fontSize: 10, marginTop: 2 }}>{item.why}</div>
              </div>
            ))}
            {plans?.interventions.length === 0 && (
              <span className="dim" style={{ fontSize: 11 }}>Nothing affordable at this budget.</span>
            )}
          </div>
        </div>

        {plans?.plan.assumptions && (
          <AssumptionLedger ledger={plans.plan.assumptions} />
        )}

        {baselines && (
          <div className="panel" style={{ padding: 10 }}>
            <h3>Versus planning without the network view</h3>
            <table className="mono" style={{ marginTop: 6 }}>
              <thead>
                <tr>
                  <th>method</th>
                  <th style={{ textAlign: "right" }}>CVaR95 on held-out years</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(baselines.results)
                  .sort((a, b) => a[1].cvar_ph - b[1].cvar_ph)
                  .map(([name, row]) => (
                    <tr key={name}>
                      <td style={{ color: name === "optimised" ? "var(--f-full)" : undefined }}>
                        {name.replace(/_/g, "-")}
                      </td>
                      <td style={{ textAlign: "right" }}>{personHours(row.cvar_ph)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            <div className="dim" style={{ fontSize: 10, marginTop: 6, lineHeight: 1.5 }}>
              The optimised package beats asset-by-asset planning by{" "}
              {baselines.optimised_vs_asset_by_asset_pct.toFixed(1)}% of tail risk at the
              same budget.
            </div>
          </div>
        )}
      </div>

      <div className="col" style={{ minHeight: 0 }}>
        <div className="row">
          <button aria-pressed={split} onClick={() => setSplit(true)}>Split comparison</button>
          <button aria-pressed={!split} onClick={() => setSplit(false)}>Single map</button>
          <span className="spacer" />
          <span className="dim mono" style={{ fontSize: 10 }}>
            {targets.size} assets targeted
          </span>
        </div>
        <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
          {split ? (
            <SplitCompare
              township={township}
              criticality={items}
              left="asset_by_asset"
              right="optimised"
            />
          ) : (
            <div style={{ position: "relative", height: "100%", overflow: "hidden", borderRadius: 3 }}>
              <DeckMap township={township} frame={frame} criticality={items} />
            </div>
          )}
        </div>
        <div className="panel" style={{ padding: 8 }}>
          <TimelineScrubber />
        </div>
      </div>
    </div>
  );
}
