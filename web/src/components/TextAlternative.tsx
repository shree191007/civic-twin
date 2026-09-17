/** A text rendering of the map state: for screen readers and for screenshots. */
import type { Frame } from "../lib/frames";
import { people, percent } from "../lib/format";

export function TextAlternative({ frame }: { frame: Frame }) {
  const down = Object.entries(frame.func)
    .filter(([, f]) => f < 0.999)
    .sort((a, b) => a[1] - b[1]);
  return (
    <details className="panel no-print" style={{ padding: 8 }}>
      <summary className="dim" style={{ fontSize: 10, cursor: "pointer" }}>
        Text alternative to the map ({down.length} assets degraded)
      </summary>
      <div style={{ fontSize: 11, marginTop: 6 }}>
        <p>
          At hour {Math.round(frame.t)}: {people(frame.totals.people_no_power)} without power,{" "}
          {people(frame.totals.people_no_water)} without water,{" "}
          {people(frame.totals.people_no_comms)} without communications,{" "}
          {people(frame.totals.people_no_health)} without hospital access.{" "}
          {frame.closed_roads.length} road segments impassable.
        </p>
        <ul className="mono" style={{ fontSize: 10, paddingLeft: 16, lineHeight: 1.6 }}>
          {down.slice(0, 25).map(([id, f]) => (
            <li key={id}>
              {id} at {percent(f)}
              {frame.reason[id] ? ` — ${frame.reason[id]}` : ""}
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}
