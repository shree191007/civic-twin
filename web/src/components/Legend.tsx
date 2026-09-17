import { css } from "../lib/colors";
import type { LegendSpec } from "../map/modes";

/** Always visible, and always states its units. */
export function Legend({ spec }: { spec: LegendSpec }) {
  return (
    <div className="panel" style={{ padding: "7px 9px", minWidth: 180 }}>
      <h3 style={{ marginBottom: 4 }}>{spec.title}</h3>
      <div className="dim" style={{ fontSize: 12, marginBottom: 6 }}>{spec.units}</div>
      {spec.continuous ? (
        <>
          <div
            style={{
              height: 8,
              borderRadius: 2,
              background: `linear-gradient(90deg, ${spec.entries.map((e) => css(e.color)).join(", ")})`,
            }}
          />
          <div className="row mono" style={{ justifyContent: "space-between", fontSize: 11, marginTop: 3 }}>
            {spec.entries.map((e, i) => (
              <span key={i}>{e.label}</span>
            ))}
          </div>
        </>
      ) : (
        <div className="col" style={{ gap: 3 }}>
          {spec.entries.map((e) => (
            <div key={e.label} className="row" style={{ gap: 6 }}>
              <span
                style={{
                  width: 9,
                  height: 9,
                  borderRadius: 2,
                  background: css(e.color),
                  flex: "0 0 auto",
                }}
              />
              <span className="mono" style={{ fontSize: 12 }}>{e.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
