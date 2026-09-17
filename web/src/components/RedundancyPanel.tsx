/** Effective Redundancy Score (patch section 12): redundancy that is not. */
import type { RedundancyGroup } from "../api/types";
import { people, percent } from "../lib/format";
import { useStore } from "../store";

function Bar({ nominal, independent }: { nominal: number; independent: number }) {
  return (
    <div className="row" style={{ gap: 2 }}>
      {Array.from({ length: nominal }, (_, i) => (
        <span
          key={i}
          title={i < independent ? "independent path" : "not independent"}
          style={{
            width: 16,
            height: 6,
            borderRadius: 1,
            background: i < independent ? "var(--f-full)" : "var(--f-down)",
          }}
        />
      ))}
    </div>
  );
}

export function RedundancyPanel({
  groups,
  systemScore,
}: {
  groups: RedundancyGroup[];
  systemScore: number;
}) {
  const selectAsset = useStore((s) => s.selectAsset);
  if (groups.length === 0) {
    return <div className="dim" style={{ fontSize: 11, padding: 10 }}>No redundancy groups.</div>;
  }
  return (
    <div className="panel" style={{ padding: 10 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3>Effective redundancy</h3>
        <span className="mono" style={{ fontSize: 11 }}>
          system {percent(systemScore)}
        </span>
      </div>
      <div className="dim" style={{ fontSize: 10, margin: "4px 0 8px", lineHeight: 1.5 }}>
        Independent failure paths as a share of the paths on paper. Two towers
        that share one substation are two on paper and one in practice.
      </div>
      <div className="col" style={{ gap: 8 }}>
        {groups.map((g) => (
          <div key={g.id} style={{ borderTop: "1px solid var(--border)", paddingTop: 6 }}>
            <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
              <span className="mono" style={{ fontSize: 11 }}>
                {g.members.join(" + ")}
              </span>
              <Bar nominal={g.nominal_paths} independent={g.independent_paths} />
            </div>
            <div className="row mono dim" style={{ fontSize: 10, gap: 10 }}>
              <span>nominal {g.nominal_paths}</span>
              <span style={{ color: g.weak ? "var(--f-critical)" : undefined }}>
                effective {g.independent_paths}
              </span>
              <span>{people(g.population)} people</span>
            </div>
            <div style={{ fontSize: 11, marginTop: 3, lineHeight: 1.45 }}>
              {g.explanation}
            </div>
            {Object.entries(g.shared_by_kind).length > 0 && (
              <div className="row" style={{ gap: 4, marginTop: 4, flexWrap: "wrap" }}>
                {Object.entries(g.shared_by_kind).map(([kind, asset]) => (
                  <button
                    key={kind}
                    className="mono"
                    style={{ fontSize: 10, padding: "1px 5px" }}
                    onClick={() => selectAsset(asset)}
                    title={`Shared ${kind}`}
                  >
                    {asset}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
