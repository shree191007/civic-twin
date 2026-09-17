/** Investment plan: choose a priority and a budget, see what to fund and why. */
import { useMemo, useState } from "react";
import { FrontierChart } from "../../components/FrontierChart";
import { ConfidenceBadge } from "../../components/ConfidenceBadge";
import { SplitCompare } from "../../components/SplitCompare";
import { TimelineScrubber } from "../../components/TimelineScrubber";
import { AssumptionLedger } from "../../components/AssumptionLedger";
import { DeckMap } from "../../map/DeckMap";
import {
  useBaselines,
  useCriticality,
  useObjectives,
  usePlans,
  useTownship,
} from "../../api/queries";
import { useScenario } from "../../lib/useScenario";
import { KIND_LABELS, usePackage, type PackageItem } from "../../lib/usePackage";
import { useStore } from "../../store";
import { inr, personHours } from "../../lib/format";

const CRORE = 1e7;
const QUICK_BUDGETS = [1, 2, 3, 5];

/** How each comparison method would describe itself to a council member. */
const METHODS: Record<string, { name: string; how: string }> = {
  optimised: { name: "This plan", how: "Network-aware: buys what protects the whole system" },
  asset_by_asset: { name: "Asset by asset", how: "Fix what fails most often, weighted by its own damage" },
  exposure: { name: "Most exposed first", how: "Fix what sits deepest in the flood" },
  centrality: { name: "Most connected first", how: "Fix what the most people depend on" },
  random: { name: "Random package", how: "Affordable measures picked at random (average)" },
};

export function Plan() {
  const { data: township } = useTownship();
  const { data: criticality } = useCriticality();
  const { data: baselines } = useBaselines();
  const { data: objectives } = useObjectives();
  const objective = useStore((s) => s.objective);
  const setObjective = useStore((s) => s.setObjective);
  const budget = useStore((s) => s.budget);
  const setBudget = useStore((s) => s.setBudget);
  const { data: plans } = usePlans(budget);
  const pkg = usePackage(budget);
  const { frame } = useScenario();
  const [split, setSplit] = useState(true);

  const sliderMax = Math.max(6 * CRORE, Math.ceil(pkg.saturationInr / CRORE + 1) * CRORE);
  const plannedFor = plans?.plan.objective;
  const selected = objectives?.objectives.find((o) => o.mode === objective);

  const groups = useMemo(() => groupByKind(pkg.items), [pkg.items]);
  const maxRemoved = Math.max(1, ...pkg.items.map((i) => i.riskRemovedPh));
  const perCrore =
    pkg.costInr > 0 ? ((pkg.cvarBeforePh - pkg.cvarAfterPh) / pkg.costInr) * CRORE : 0;

  if (!township) return <div className="empty">Loading the township…</div>;

  return (
    <div className="page">
      <div className="page-inner">
        <header className="page-header">
          <div>
            <h1 className="page-title">Investment plan</h1>
            <p className="page-lede">
              Pick what matters most and how much you can spend. The model shows which
              measures to fund, in what order, and how much storm damage each one prevents.
            </p>
          </div>
        </header>

        {/* ------------------------------------------------------ 1. priority */}
        <section className="section" aria-labelledby="priority-title">
          <div className="section-head">
            <div>
              <h2 className="section-title" id="priority-title">
                <span className="step-num">1</span>What should the money protect first?
              </h2>
              <p className="section-sub">This changes what gets bought, not just how the result is described.</p>
            </div>
          </div>
          <div className="choice-grid" role="radiogroup" aria-labelledby="priority-title">
            {(objectives?.objectives ?? []).map((o) => (
              <label
                key={o.mode}
                className={objective === o.mode ? "choice choice-selected" : "choice"}
              >
                <input
                  type="radio"
                  name="objective"
                  checked={objective === o.mode}
                  onChange={() => setObjective(o.mode)}
                />
                <span className="choice-title">
                  <span className="radio-dot" aria-hidden="true" />
                  {o.label}
                </span>
                <span className="choice-desc" style={{ display: "block" }}>
                  {o.description}
                </span>
              </label>
            ))}
          </div>
          {plannedFor && selected && plannedFor.mode !== objective && (
            <div className="callout" role="status">
              <span aria-hidden="true">⚠</span>
              <span>
                The figures below were optimised for <strong>{plannedFor.label.toLowerCase()}</strong>.
                To see the package for <strong>{selected.label.toLowerCase()}</strong>, re-run the
                analysis with <code>--objective {objective}</code>.
              </span>
            </div>
          )}
        </section>

        {/* -------------------------------------------------------- 2. budget */}
        <section className="section" aria-labelledby="budget-title">
          <div className="section-head">
            <div>
              <h2 className="section-title" id="budget-title">
                <span className="step-num">2</span>How much can you spend?
              </h2>
              <p className="section-sub">
                Each step on the curve is one more measure. Where it flattens, extra money buys very little.
              </p>
            </div>
          </div>
          <div className="budget-grid">
            <div className="card">
              <div className="kpi-label">Budget</div>
              <div className="budget-value mono">{inr(budget)}</div>
              <input
                className="budget-range"
                type="range"
                min={0}
                max={sliderMax}
                step={10 * 100_000}
                value={Math.min(budget, sliderMax)}
                onChange={(e) => setBudget(Number(e.target.value))}
                aria-label="Resilience budget in rupees"
              />
              <div className="budget-ticks mono">
                <span>₹0</span>
                <span>{inr(sliderMax)}</span>
              </div>
              <div className="chip-row">
                {QUICK_BUDGETS.map((c) => (
                  <button
                    key={c}
                    className="chip"
                    aria-pressed={budget === c * CRORE}
                    onClick={() => setBudget(c * CRORE)}
                  >
                    ₹{c} crore
                  </button>
                ))}
              </div>
              <div style={{ marginTop: 18 }}>
                <div className="row" style={{ justifyContent: "space-between", fontSize: 13 }}>
                  <span className="dim">Allocated</span>
                  <span className="mono">
                    {inr(pkg.costInr)} of {inr(budget)}
                  </span>
                </div>
                <div className="meter" style={{ marginTop: 6 }}>
                  <span style={{ width: `${budget > 0 ? Math.min(100, (100 * pkg.costInr) / budget) : 0}%` }} />
                </div>
              </div>
              {pkg.saturationInr > 0 && budget > pkg.saturationInr && (
                <div className="callout callout-info">
                  <span aria-hidden="true">ℹ</span>
                  <span>
                    Above <strong>{inr(pkg.saturationInr)}</strong> the model finds nothing else worth
                    buying. The rest of this budget stays unallocated.
                  </span>
                </div>
              )}
            </div>
            <div className="card">
              <FrontierChart steps={pkg.steps} budget={budget} cvarBefore={pkg.cvarBeforePh} />
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------- 3. outcome */}
        <section className="section" aria-labelledby="outcome-title">
          <div className="section-head">
            <h2 className="section-title" id="outcome-title">
              <span className="step-num">3</span>What this package achieves
            </h2>
          </div>
          <div className="kpi-grid">
            <div className="card kpi">
              <div className="kpi-label">Worst-year damage reduced by</div>
              <div className="kpi-value kpi-good">{pkg.reductionPct.toFixed(1)}%</div>
              <div className="kpi-sub">
                From {personHours(pkg.cvarBeforePh)} to {personHours(pkg.cvarAfterPh)} in the worst 1-in-20 years
              </div>
            </div>
            <div className="card kpi">
              <div className="kpi-label">Measures funded</div>
              <div className="kpi-value">{pkg.items.length}</div>
              <div className="kpi-sub">
                {groups.map((g) => `${g.items.length} ${g.label.toLowerCase()}`).join(" · ") || "Nothing yet"}
              </div>
            </div>
            <div className="card kpi">
              <div className="kpi-label">Cost</div>
              <div className="kpi-value mono">{inr(pkg.costInr)}</div>
              <div className="kpi-sub">
                {budget > pkg.costInr ? `${inr(budget - pkg.costInr)} left unallocated` : "Budget fully used"}
              </div>
            </div>
            <div className="card kpi">
              <div className="kpi-label">Damage avoided per ₹1 crore</div>
              <div className="kpi-value mono">{compact(perCrore)}</div>
              <div className="kpi-sub">
                person-hours, on average. The last measure bought returns{" "}
                {compact(pkg.lastMarginalPerCrore)} per crore.
              </div>
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------- 4. package */}
        <section className="section" aria-labelledby="package-title">
          <div className="section-head">
            <div>
              <h2 className="section-title" id="package-title">
                <span className="step-num">4</span>The recommended package
              </h2>
              <p className="section-sub">
                Grouped by type. The number shows the order the optimiser chose them in. Confidence is
                how often the measure was picked across 20 plausible versions of the town.
              </p>
            </div>
          </div>
          <div className="card" style={{ padding: "6px 8px" }}>
            {pkg.items.length === 0 ? (
              <div className="empty">Move the budget above ₹0 to see a package.</div>
            ) : (
              <table className="package-table">
                <thead>
                  <tr>
                    <th style={{ width: 44 }}>#</th>
                    <th>Measure and what it protects</th>
                    <th className="num-right" style={{ width: 120 }}>Cost</th>
                    <th style={{ width: 200 }}>Damage avoided</th>
                    <th style={{ width: 110 }}>Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((group) => (
                    <GroupRows key={group.kind} group={group} maxRemoved={maxRemoved} />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>

        {/* ------------------------------------------------ 5. versus others */}
        {baselines && (
          <section className="section" aria-labelledby="compare-title">
            <div className="section-head">
              <div>
                <h2 className="section-title" id="compare-title">
                  <span className="step-num">5</span>How it compares with simpler ways of choosing
                </h2>
                <p className="section-sub">
                  Every method gets the same {inr(baselines.budget_inr)} and is tested on storm years the
                  optimiser never saw. Longer bars remove more worst-year damage.
                </p>
              </div>
            </div>
            <div className="card">
              <MethodBars baselines={baselines} />
            </div>
          </section>
        )}

        {/* ----------------------------------------------------- 6. in a storm */}
        <section className="section" aria-labelledby="storm-title">
          <div className="section-head">
            <div>
              <h2 className="section-title" id="storm-title">
                <span className="step-num">6</span>Watch it in a 1-in-50-year storm
              </h2>
              <p className="section-sub">
                Press play. The counters show how many people lose each service with each approach.
              </p>
            </div>
            <div className="segmented" role="group" aria-label="Map layout">
              <button aria-pressed={split} onClick={() => setSplit(true)}>Side by side</button>
              <button aria-pressed={!split} onClick={() => setSplit(false)}>Single map</button>
            </div>
          </div>
          <div className="card" style={{ padding: 10 }}>
            <div style={{ height: 520, position: "relative" }}>
              {split ? (
                <SplitCompare
                  township={township}
                  criticality={criticality?.items ?? []}
                  left="asset_by_asset"
                  right="optimised"
                />
              ) : (
                <div style={{ position: "relative", height: "100%", overflow: "hidden", borderRadius: 8 }}>
                  <DeckMap township={township} frame={frame} criticality={criticality?.items ?? []} />
                </div>
              )}
            </div>
            <div style={{ padding: "12px 6px 2px" }}>
              <TimelineScrubber />
            </div>
          </div>
        </section>

        {plans?.plan.assumptions && (
          <section className="section">
            <AssumptionLedger ledger={plans.plan.assumptions} />
          </section>
        )}
      </div>
    </div>
  );
}

/** 471,000 -> "471k", 1,250,000 -> "1.25M": big numbers that fit a tile. */
function compact(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (Math.abs(n) >= 1e3) return `${Math.round(n / 1e3)}k`;
  return `${Math.round(n)}`;
}

interface Group {
  kind: string;
  label: string;
  items: PackageItem[];
  costInr: number;
}

function groupByKind(items: PackageItem[]): Group[] {
  const map = new Map<string, Group>();
  for (const item of items) {
    const group = map.get(item.kind) ?? {
      kind: item.kind,
      label: KIND_LABELS[item.kind] ?? item.kind,
      items: [],
      costInr: 0,
    };
    group.items.push(item);
    group.costInr += item.costInr;
    map.set(item.kind, group);
  }
  return [...map.values()].sort((a, b) => a.items[0].rank - b.items[0].rank);
}

function GroupRows({ group, maxRemoved }: { group: Group; maxRemoved: number }) {
  return (
    <>
      <tr className="group-row">
        <td colSpan={2}>
          {group.label} · {group.items.length}
        </td>
        <td className="num-right mono">{inr(group.costInr)}</td>
        <td colSpan={2} />
      </tr>
      {group.items.map((item) => (
        <tr key={item.id}>
          <td className="rank">{item.rank}</td>
          <td>
            <div className="measure-name">{item.label}</div>
            {item.why && <div className="measure-why">{item.why}</div>}
          </td>
          <td className="num-right mono">{inr(item.costInr)}</td>
          <td>
            <span className="mono" style={{ fontSize: 13 }}>{personHours(item.riskRemovedPh)}</span>
            <div className="impact-bar">
              <span style={{ width: `${(100 * item.riskRemovedPh) / maxRemoved}%` }} />
            </div>
          </td>
          <td>
            {item.confidence == null ? (
              <span className="dim" style={{ fontSize: 13 }} title="Only scored for measures in the precomputed plan">
                not scored
              </span>
            ) : (
              <ConfidenceBadge value={item.confidence} />
            )}
          </td>
        </tr>
      ))}
    </>
  );
}

function MethodBars({ baselines }: { baselines: NonNullable<ReturnType<typeof useBaselines>["data"]> }) {
  const base = baselines.baseline_cvar_ph;
  const rows = Object.entries(baselines.results)
    .map(([key, r]) => ({
      key,
      ...(METHODS[key] ?? { name: key.replace(/_/g, " "), how: "" }),
      cvar: r.cvar_ph,
      reduction: base > 0 ? Math.max(0, (100 * (base - r.cvar_ph)) / base) : 0,
    }))
    .sort((a, b) => b.reduction - a.reduction);
  const top = Math.max(1, ...rows.map((r) => r.reduction));
  const optimised = rows.find((r) => r.key === "optimised");
  const assetByAsset = rows.find((r) => r.key === "asset_by_asset");
  const gap = baselines.optimised_vs_asset_by_asset_pct;

  return (
    <>
      {rows.map((r) => (
        <div key={r.key} className={r.key === "optimised" ? "method-row method-best" : "method-row"}>
          <div className="method-name">
            {r.name}
            <small>{r.how}</small>
          </div>
          <div className="method-track" aria-hidden="true">
            <span style={{ width: `${(100 * r.reduction) / top}%` }} />
          </div>
          <div className="method-value mono">−{r.reduction.toFixed(1)}%</div>
        </div>
      ))}
      {optimised && assetByAsset && (
        <div className={gap >= 5 ? "callout callout-info" : "callout"} style={{ marginTop: 14 }}>
          <span aria-hidden="true">{gap >= 5 ? "ℹ" : "⚠"}</span>
          <span>
            {gap > 0 ? (
              <>
                The network-aware plan leaves <strong>{gap.toFixed(1)}% less</strong> worst-year damage than
                choosing asset by asset.
                {gap < 5 &&
                  " That margin is small: in this town the obvious weak points and the systemic ones largely overlap."}
              </>
            ) : (
              <>
                On these held-out years the network-aware plan does <strong>not</strong> beat choosing asset by
                asset. Treat the ranking with caution.
              </>
            )}
          </span>
        </div>
      )}
    </>
  );
}
