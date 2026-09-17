/** The council brief, laid out as a document. The browser makes the PDF. */
import {
  useBaselines,
  useRedundancy,
  useRisk,
  useSpofs,
  useTownship,
} from "../api/queries";
import { inr, people, percent, personHours } from "../lib/format";
import { usePackage } from "../lib/usePackage";
import { useStore } from "../store";
import type { Estimate } from "../api/types";

const SERVICE_NAMES: Record<string, string> = {
  energy: "power",
  water: "water",
  comms: "telecom",
  health: "hospital access",
  mobility: "road access",
};

/** A range, written the way it should be read aloud. */
function range(estimate: Estimate | undefined, format: (n: number) => string): string {
  if (!estimate) return "—";
  return `${format(estimate.low)} – ${format(estimate.high)}`;
}

/** "1.84–2.26 M": a range short enough for a figure box; the unit goes beneath. */
function compactRange(estimate: Estimate | undefined): string {
  if (!estimate) return "—";
  const scale = Math.max(estimate.low, estimate.high) >= 1e6 ? 1e6 : 1e3;
  const unit = scale === 1e6 ? "M" : "k";
  const digits = scale === 1e6 ? 2 : 0;
  return `${(estimate.low / scale).toFixed(digits)}–${(estimate.high / scale).toFixed(digits)} ${unit}`;
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
  const pkg = usePackage(budget);

  if (!township || !risk) {
    return <div className="paper empty">The brief needs the analysis results. Run the analysis first.</div>;
  }

  const prepared = new Date().toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" });
  const topService = Object.entries(risk.service_contributions_ph).sort((a, b) => b[1] - a[1])[0];
  const peopleLow = roundOut(risk.people_affected?.low ?? 0, false);
  const peopleHigh = roundOut(risk.people_affected?.high ?? 0, true);
  const weakGroups = (redundancy?.groups ?? []).filter((g) => g.weak);
  const gap = baselines?.optimised_vs_asset_by_asset_pct ?? 0;
  const totalAssets = Object.values(township.provenance_summary).reduce((a, b) => a + b, 0);

  return (
    <article className="paper brief">
      <div className="brief-eyebrow">Council brief · Monsoon flood resilience</div>
      <h1>{township.name}: what a severe flood would do, and what to fund first</h1>
      <div className="brief-meta">
        Prepared {prepared} · Budget considered {inr(budget)} · Model data {township.data_version}.
        All figures are modelled ranges, not forecasts of a specific storm.
      </div>

      <div className="brief-kpis">
        <div className="brief-kpi">
          <div className="label">Service lost in a typical year</div>
          <div className="value mono">{compactRange(risk.eal)}</div>
          <div className="sub">person-hours · {risk.eal?.confidence ?? "unknown"} confidence</div>
        </div>
        <div className="brief-kpi">
          <div className="label">In the worst 1-in-20 years</div>
          <div className="value mono">{compactRange(risk.cvar95)}</div>
          <div className="sub">person-hours · up to {people(peopleHigh)} people affected</div>
        </div>
        <div className="brief-kpi">
          <div className="label">Recommended package</div>
          <div className="value mono">{inr(pkg.costInr)}</div>
          <div className="sub">
            {pkg.items.length} measures · worst-year damage −{pkg.reductionPct.toFixed(1)}%
          </div>
        </div>
      </div>

      <h2><span className="n">1</span>The exposure</h2>
      <p>
        Across {risk.n_scenarios.toLocaleString("en-IN")} simulated monsoon years, most pass
        with little disruption{peopleLow > 0 ? ` (still ${people(peopleLow)} people affected at the low end)` : ""},
        but in a severe year <strong>up to {people(peopleHigh)} people</strong> lose at least one
        essential service. Over a typical year that adds up to {range(risk.eal, personHours)} of
        lost service. The largest share of the damage is loss of{" "}
        <strong>{SERVICE_NAMES[topService?.[0] ?? ""] ?? topService?.[0]}</strong>, and zone{" "}
        <strong>{risk.worst_zone}</strong> is hit hardest.
      </p>
      <p className="fineprint">
        The spread in these figures comes from{" "}
        {(risk.cvar95?.drivers ?? []).map((d) => d.replace(/_/g, " ")).join(", ")}.
      </p>

      <h2><span className="n">2</span>Weak points a normal inspection would miss</h2>
      <ul>
        {(spofs?.items ?? []).slice(0, 3).map((s, i) => (
          <li key={i}>
            <strong>{people(s.affected_population)} people</strong> — {s.explanation}
            {s.counterfactual_ph > 0 && (
              <> Protecting it avoids about {personHours(s.counterfactual_ph)} of lost service.</>
            )}
          </li>
        ))}
      </ul>
      {redundancy && weakGroups.length > 0 && (
        <p>
          Of {redundancy.groups.length} sets of equipment meant to back each other up,{" "}
          <strong>{weakGroups.length}</strong> share a hidden common cause. Across the town only{" "}
          {percent(redundancy.system_score)} of the backup on paper is genuinely independent. For
          example: {weakGroups[0].explanation}
        </p>
      )}

      <h2><span className="n">3</span>What we recommend funding</h2>
      {pkg.items.length === 0 ? (
        <p>No measures are affordable within the budget considered.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th style={{ width: 28 }}>#</th>
              <th>Measure</th>
              <th className="num-right">Cost</th>
              <th className="num-right">Damage avoided</th>
              <th className="num-right">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {pkg.items.map((item) => (
              <tr key={item.id}>
                <td className="mono">{item.rank}</td>
                <td>{item.label}</td>
                <td className="num-right mono">{inr(item.costInr)}</td>
                <td className="num-right mono">{personHours(item.riskRemovedPh)}</td>
                <td className="num-right mono">
                  {item.confidence == null ? "—" : percent(item.confidence)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td />
              <td>Total</td>
              <td className="num-right mono">{inr(pkg.costInr)}</td>
              <td className="num-right mono">−{pkg.reductionPct.toFixed(1)}%</td>
              <td />
            </tr>
          </tfoot>
        </table>
      )}
      <p className="fineprint" style={{ marginTop: 6 }}>
        Confidence is how often a measure was chosen across 20 plausible versions of the town's
        networks. Costs are illustrative unit rates, not tendered prices.
      </p>

      {baselines && (
        <>
          <h2><span className="n">4</span>Why this package and not another</h2>
          <p>
            Spending the same {inr(baselines.budget_inr)} by fixing whatever fails most often — the
            usual asset-by-asset approach — was tested against this plan on storm years neither had
            seen.{" "}
            {gap > 0 ? (
              <>
                This plan leaves <strong>{gap.toFixed(1)}% less</strong> worst-year damage.
                {gap < 5 &&
                  " The margin is modest: in this town the obvious weak points and the systemic ones largely overlap."}
              </>
            ) : (
              <>
                On those years this plan did <strong>not</strong> outperform the asset-by-asset approach,
                so the ranking should be treated with caution.
              </>
            )}
          </p>
        </>
      )}

      <h2><span className="n">5</span>What these figures rest on</h2>
      {risk.assumptions && (
        <>
          <p>
            A claim is only as firm as its weakest input. Here that is{" "}
            <strong>{risk.assumptions.standing.replace(/_/g, " ")}</strong>.
          </p>
          <table>
            <thead>
              <tr>
                <th>Input</th>
                <th>Basis</th>
                <th className="num-right">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {risk.assumptions.assumptions.map((a, i) => (
                <tr key={i}>
                  <td style={{ textTransform: "capitalize" }}>{a.subject}</td>
                  <td>{a.statement}</td>
                  <td className="num-right">{a.evidence.replace(/_/g, " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <p className="fineprint" style={{ marginTop: 18 }}>
        Of {totalAssets} modelled assets, {township.provenance_summary.observed} are observed directly,{" "}
        {township.provenance_summary.inferred} are inferred and {township.provenance_summary.synthetic} are
        synthetic. Figures are modelled loss of service, not casualties. The model has not yet been
        validated against a recorded flood.
      </p>
    </article>
  );
}
