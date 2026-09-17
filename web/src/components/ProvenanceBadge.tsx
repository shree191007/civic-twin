import type { Provenance } from "../api/types";

const LABEL: Record<Provenance, string> = {
  observed: "observed",
  inferred: "inferred",
  synthetic: "synthetic",
};

/** Provenance is never hidden: the user must always know what is made up. */
export function ProvenanceBadge({ value }: { value: Provenance }) {
  return (
    <span
      className="mono"
      title={`This asset is ${LABEL[value]} data`}
      style={{
        fontSize: 9,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        padding: "1px 5px",
        borderRadius: 2,
        border: `1px solid var(--prov-${value})`,
        color: `var(--prov-${value})`,
      }}
    >
      {LABEL[value]}
    </span>
  );
}
