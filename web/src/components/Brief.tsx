/** The one-page council brief. Print-styled; the browser makes the PDF. */
import {
  useBaselines,
  usePlans,
  useRedundancy,
  useRisk,
  useSpofs,
  useTownship,
} from "../api/queries";
import { inr, people, percent, personHours } from "../lib/format";
import { useStore } from "../store";
import type { Estimate } from "../api/types";

/** A range, printed the way it should be read aloud. */
function range(estimate: Estimate | undefined, format: (n: number) => string): string {
  if (!estimate) return "—";
  return `${format(estimate.low)} to ${format(estimate.high)}`;
}

function roundOut(value: number, up: boolean): number {
  const step = value >= 100_000 ? 5_000 : value >= 10_000 ? 1_000 : 100;
  return (up ? Math.ceil(value / step) : Math.floor(value / step)) * step;
}

export function Brief() {
  const { data: township } = useTownship();
  const { data: risk } = useRisk();
  const { data: spofs } = useSpofs();
  const { data: baselines } = useBaselines();
  const { data: redundancy } = useRedundancy();
  const budget = useStore((s) => s.budget);
  const { data: plans } = usePlans(budget);

  if (!township || !risk || !plans) {
    return <div className="dim" style={{ padding: 10 }}>Brief needs the analysis results.</div>;
  }

  return (
    <article className="brief panel" style={{ padding: 16 }}>
      <h1>{township.name} — monsoon resilience brief</h1>
      <div className="dim mono" style={{ fontSize: 10 }}>
        data {township.data_version} · model {township.model_version} · budget {inr(plans.plan.cost_inr)}
      </div>

      <h2>The exposure</h2>
      <p>
        Across {risk.n_scenarios} simulated monsoon years, the township loses{" "}
        <strong>{range(risk.eal, personHours)}</strong> of service in a typical
        year ({risk.eal?.confidence ?? "unknown"} confidence). In the worst{" "}
        {percent(1 - risk.alpha)} of years that rises to{" "}
        <strong>{range(risk.cvar95, personHours)}</strong>, affecting{" "}
        <strong>
          {people(roundOut(risk.people_affected?.low ?? 0, false))} to{" "}
          {people(roundOut(risk.people_affected?.high ?? 0, true))} people
        </strong>
        . The heaviest single contribution comes from{" "}
        {topService(risk.service_contributions_ph)}, and zone {risk.worst_zone}{" "}
        carries the most.
      </p>
      <p className="dim">
        These are ranges, not point estimates, because the inputs are ranges.
        The spread is driven by{" "}
        {(risk.cvar95?.drivers ?? []).map((d) => d.replace(/_/g, " ")).join(", ")}.
      </p>

      <h2>What asset-by-asset monitoring misses</h2>
      <ul>
        {(spofs?.items ?? []).slice(0, 3).map((s, i) => (
          <li key={i} style={{ marginBottom: 4 }}>
            <strong>{people(s.affected_population)} people</strong> — {s.explanation}
            {s.counterfactual_ph > 0 && (
              <> Measured by removing it: {personHours(s.counterfactual_ph)}.</>
            )}
          </li>
        ))}
      </ul>

      {redundancy && (
        <>
          <h2>Redundancy that is not redundant</h2>
          <p>
            Across the township, {percent(redundancy.system_score)} of the
            redundancy on paper is genuinely independent;{" "}
            {redundancy.weak_groups} of {redundancy.groups.length} groups have
            fewer real failure paths than nominal ones.
          </p>
          <table>
            <thead>
              <tr>
                <th>group</th>
                <th style={{ textAlign: "right" }}>nominal</th>
                <th style={{ textAlign: "right" }}>effective</th>
                <th>shared cause</th>
              </tr>
            </thead>
            <tbody>
              {redundancy.groups
                .filter((g) => g.weak)
                .slice(0, 4)
                .map((g) => (
                  <tr key={g.id}>
                    <td className="mono">{g.members.join(" + ")}</td>
                    <td className="mono" style={{ textAlign: "right" }}>
                      {g.nominal_paths}
                    </td>
                    <td className="mono" style={{ textAlign: "right" }}>
                      {g.independent_paths}
                    </td>
                    <td>
                      {Object.entries(g.shared_by_kind)
                        .map(([kind, asset]) => `${asset} (${kind})`)
                        .join(", ")}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </>
      )}

      <h2>Recommended package</h2>
      <table>
        <thead>
          <tr>
            <th>measure</th>
            <th style={{ textAlign: "right" }}>cost</th>
            <th style={{ textAlign: "right" }}>tail risk avoided</th>
            <th style={{ textAlign: "right" }}>confidence</th>
          </tr>
        </thead>
        <tbody>
          {plans.interventions.map((item) => (
            <tr key={item.id}>
              <td>{item.label}</td>
              <td className="mono" style={{ textAlign: "right" }}>{inr(item.cost_inr)}</td>
              <td className="mono" style={{ textAlign: "right" }}>{personHours(item.cvar_reduction_ph)}</td>
              <td className="mono" style={{ textAlign: "right" }}>
                {item.selection_frequency == null ? "—" : percent(item.selection_frequency)}
              </td>
            </tr>
          ))}
          <tr>
            <td><strong>total</strong></td>
            <td className="mono" style={{ textAlign: "right" }}><strong>{inr(plans.plan.cost_inr)}</strong></td>
            <td className="mono" style={{ textAlign: "right" }}>
              <strong>−{plans.plan.cvar_reduction_pct.toFixed(1)}%</strong>
            </td>
            <td />
          </tr>
        </tbody>
      </table>

      {baselines && (
        <>
          <h2>Why this package and not another</h2>
          <p>
            Spending the same {inr(baselines.budget_inr)} on the assets that fail most often —
            the conventional asset-by-asset ranking — leaves tail risk at{" "}
            {personHours(baselines.results.asset_by_asset?.cvar_ph ?? 0)}. This package reaches{" "}
            {personHours(baselines.results.optimised?.cvar_ph ?? 0)}, an improvement of{" "}
            {baselines.optimised_vs_asset_by_asset_pct.toFixed(1)}%, measured on{" "}
            {baselines.evaluated_on} years the optimiser never saw.
          </p>
        </>
      )}

      <h2>What this rests on</h2>
      {risk.assumptions && (
        <>
          <p>
            The strongest claim here is only as firm as its weakest input, which
            is <strong>{risk.assumptions.standing}</strong>.
          </p>
          <table>
            <thead>
              <tr>
                <th>input</th>
                <th>basis</th>
                <th style={{ textAlign: "right" }}>evidence</th>
              </tr>
            </thead>
            <tbody>
              {risk.assumptions.assumptions.map((a, i) => (
                <tr key={i}>
                  <td>{a.subject}</td>
                  <td>{a.statement}</td>
                  <td className="mono" style={{ textAlign: "right" }}>
                    {a.evidence.replace(/_/g, " ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2>Caveats</h2>
      <p className="dim">
        {township.provenance_summary.synthetic} of {Object.values(township.provenance_summary).reduce((a, b) => a + b, 0)}{" "}
        assets are synthetic and {township.provenance_summary.inferred} are inferred; only{" "}
        {township.provenance_summary.observed} are observed directly. Costs are illustrative
        unit rates, not tendered prices. Figures are modelled service loss, not casualties.
        The model has not been validated against a real flood; the replay harness
        exists and awaits a documented event.
      </p>
    </article>
  );
}

function topService(contributions: Record<string, number>): string {
  const entries = Object.entries(contributions).sort((a, b) => b[1] - a[1]);
  return entries.length ? `${entries[0][0]} (${personHours(entries[0][1])})` : "—";
}
