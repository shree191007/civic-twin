/** One hidden single point of failure, in the copy format from spec 05 section 4.3. */
import type { Spof } from "../api/types";
import { people, percent, personHours } from "../lib/format";

const TITLE: Record<Spof["kind"], string> = {
  shared_dependency: "FAKE REDUNDANCY",
  colocation: "CO-LOCATION",
  common_cause: "COMMON CAUSE",
  recovery: "RECOVERY CHOKE POINT",
};

export function SpofCard({ spof, onShow }: { spof: Spof; onShow: (s: Spof) => void }) {
  const group = spof.redundant_group;
  const groupText =
    group.length === 2 ? `${group[0]} and ${group[1]}` : group.slice(0, 4).join(", ");
  const zones = spof.affected_zones;
  const zoneText =
    zones.length > 2 ? `${zones[0]}–${zones[zones.length - 1]}` : zones.join(", ");

  return (
    <div className="panel" style={{ padding: 10 }}>
      <div className="mono" style={{ fontSize: 11, letterSpacing: "0.07em", color: "var(--f-critical-text)" }}>
        {TITLE[spof.kind]} · {people(spof.affected_population)} people
      </div>
      <div style={{ fontSize: 13, marginTop: 5, lineHeight: 1.5 }}>
        {group.length > 1 && (
          <>
            {groupText} both serve {zoneText || "the same zones"} for {spof.service}.<br />
          </>
        )}
        {spof.explanation}
      </div>
      <div className="dim mono" style={{ fontSize: 12, marginTop: 6 }}>
        {spof.counterfactual_ph > 0 ? (
          <>
            Measured by removing it: {personHours(spof.counterfactual_ph)} lost,
            {" "}
            {personHours(spof.counterfactual_storm_ph)} of that on top of the design storm.
          </>
        ) : (
          <>Removing it changes nothing measurable — structural only.</>
        )}
      </div>
      <div className="row" style={{ marginTop: 8, justifyContent: "space-between" }}>
        <span className="dim mono" style={{ fontSize: 12 }}>
          P(fails at design storm) {percent(spof.design_storm_failure_prob, 0)}
        </span>
        <button onClick={() => onShow(spof)}>Show me</button>
      </div>
    </div>
  );
}
