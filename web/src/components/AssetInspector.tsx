/** The right-hand panel in /explore: everything known about one asset. */
import { useAssumptions, useAsset, useTrace } from "../api/queries";
import { AssumptionLedger } from "./AssumptionLedger";
import { OPERATING_COLOR } from "../lib/colors";
import { useStore } from "../store";
import { people, percent, personHours } from "../lib/format";
import { ProvenanceBadge } from "./ProvenanceBadge";
import { DependencyGraph } from "./DependencyGraph";
import { functionalityColor, css } from "../lib/colors";
import type { Frame } from "../lib/frames";

function Chips({ ids, onPick }: { ids: string[]; onPick: (id: string) => void }) {
  if (ids.length === 0) return <span className="dim">none</span>;
  return (
    <div className="row" style={{ flexWrap: "wrap", gap: 4 }}>
      {ids.map((id) => (
        <button
          key={id}
          className="mono"
          style={{ fontSize: 10, padding: "1px 5px" }}
          onClick={() => onPick(id)}
        >
          {id}
        </button>
      ))}
    </div>
  );
}

export function AssetInspector({ frame }: { frame: Frame }) {
  const selected = useStore((s) => s.selectedAsset);
  const selectAsset = useStore((s) => s.selectAsset);
  const { data: detail, isLoading } = useAsset(selected);
  const { data: trace } = useTrace(selected, "down");
  const { data: evidence } = useAssumptions("asset", selected);

  if (!selected) {
    return (
      <aside className="panel scroll" style={{ padding: 12 }}>
        <h3>Asset inspector</h3>
        <p className="dim" style={{ fontSize: 11, lineHeight: 1.5 }}>
          Click any asset on the map to trace what it carries. The arcs will show
          the chain of causation travelling toward everything that depends on it.
        </p>
      </aside>
    );
  }
  if (isLoading || !detail) {
    return <aside className="panel" style={{ padding: 12 }}><span className="dim">Loading…</span></aside>;
  }

  const a = detail.asset;
  const f = frame.func[a.id] ?? 1;
  const state = frame.state[a.id] ?? "operational";
  const crit = detail.criticality;

  return (
    <aside className="panel scroll" style={{ padding: 12 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 className="mono">{a.id}</h2>
        <ProvenanceBadge value={a.provenance} />
      </div>
      <div style={{ fontSize: 12, marginTop: 2 }}>{a.name}</div>
      <div className="dim mono" style={{ fontSize: 10 }}>
        {a.kind} · {a.portfolio}
      </div>

      <div className="row" style={{ gap: 16, marginTop: 10 }}>
        <div className="col" style={{ gap: 0 }}>
          <span className="mono" style={{ fontSize: 16, color: css(functionalityColor(f)) }}>
            {percent(f)}
          </span>
          <span className="dim" style={{ fontSize: 9 }}>functionality</span>
        </div>
        <div className="col" style={{ gap: 0 }}>
          <span className="mono" style={{ fontSize: 16 }}>{people(a.served_population)}</span>
          <span className="dim" style={{ fontSize: 9 }}>people served</span>
        </div>
      </div>
      <div className="row" style={{ gap: 8, marginTop: 6 }}>
        <span
          className="mono"
          title="How it is running, as distinct from how damaged it is"
          style={{
            fontSize: 9,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            padding: "1px 5px",
            borderRadius: 2,
            border: `1px solid ${css(OPERATING_COLOR[state] ?? OPERATING_COLOR.operational)}`,
            color: css(OPERATING_COLOR[state] ?? OPERATING_COLOR.operational),
          }}
        >
          {state.replace("_", " ")}
        </span>
        {frame.reason[a.id] && (
          <span className="mono" style={{ fontSize: 10, color: "var(--f-critical)" }}>
            cause: {frame.reason[a.id]}
          </span>
        )}
      </div>

      {crit && (
        <>
          <h3 style={{ marginTop: 14 }}>Criticality</h3>
          <table className="mono" style={{ fontSize: 11 }}>
            <tbody>
              <tr>
                <td className="dim">tail criticality</td>
                <td style={{ textAlign: "right" }}>{personHours(crit.tail_criticality_ph)}</td>
              </tr>
              <tr>
                <td className="dim">annual failure prob.</td>
                <td style={{ textAlign: "right" }}>{percent(crit.annual_failure_prob, 1)}</td>
              </tr>
              <tr>
                <td className="dim">systemic ratio</td>
                <td style={{ textAlign: "right" }}>
                  {crit.systemic_ratio.toFixed(2)}
                  {crit.systemic && (
                    <span style={{ color: "var(--f-critical)", marginLeft: 5 }}>systemic</span>
                  )}
                </td>
              </tr>
              {crit.systemic_risk && (
                <>
                  <tr>
                    <td className="dim">dependency concentration</td>
                    <td style={{ textAlign: "right" }}>
                      {percent(crit.systemic_risk.dependency_concentration)}
                    </td>
                  </tr>
                  <tr>
                    <td className="dim">recovery difficulty</td>
                    <td style={{ textAlign: "right" }}>
                      {percent(crit.systemic_risk.recovery_difficulty)}
                    </td>
                  </tr>
                </>
              )}
            </tbody>
          </table>
        </>
      )}

      {detail.explanations && detail.explanations.length > 0 && (
        <>
          <h3 style={{ marginTop: 14 }}>What it carries</h3>
          <ul className="mono" style={{ fontSize: 10, paddingLeft: 14, margin: 0, lineHeight: 1.6 }}>
            {detail.explanations.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </>
      )}

      {detail.spofs && detail.spofs.length > 0 && (
        <>
          <h3 style={{ marginTop: 14 }}>Single points of failure</h3>
          {detail.spofs.map((s, i) => (
            <div key={i} style={{ fontSize: 11, marginBottom: 6, lineHeight: 1.45 }}>
              <span className="mono" style={{ color: "var(--f-critical)", fontSize: 9 }}>
                {s.kind.replace("_", " ").toUpperCase()} · {people(s.affected_population)} people
              </span>
              <div className="dim">{s.explanation}</div>
            </div>
          ))}
        </>
      )}

      <h3 style={{ marginTop: 14 }}>Upstream</h3>
      <Chips ids={detail.upstream ?? []} onPick={selectAsset} />
      <h3 style={{ marginTop: 10 }}>Downstream</h3>
      <Chips ids={detail.downstream ?? []} onPick={selectAsset} />

      <h3 style={{ marginTop: 14 }}>
        Dependency chain{trace ? ` · ${people(trace.affected_population)} people` : ""}
      </h3>
      <DependencyGraph trace={trace} />

      <div style={{ marginTop: 12 }}>
        <AssumptionLedger ledger={evidence?.ledger} />
      </div>
    </aside>
  );
}
