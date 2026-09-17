/** "Why does Gotham think this?" — the assumption ledger (patch section 7). */
import type { Evidence, Ledger, ProvenanceSummary } from "../api/types";

const EVIDENCE_COLOR: Record<Evidence, string> = {
  observed: "var(--prov-observed)",
  verified: "var(--f-full-text)",
  estimated: "var(--f-degraded-text)",
  external_model: "var(--water-text)",
  simulated: "var(--comms)",
  assumed: "var(--f-critical-text)",
};

const EVIDENCE_LABEL: Record<Evidence, string> = {
  observed: "observed",
  verified: "verified",
  estimated: "estimated",
  external_model: "external model",
  simulated: "simulated",
  assumed: "assumed",
};

export function EvidenceChip({ value }: { value: Evidence }) {
  return (
    <span
      className="mono"
      style={{
        fontSize: 11,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        padding: "0 4px",
        borderRadius: 2,
        border: `1px solid ${EVIDENCE_COLOR[value]}`,
        color: EVIDENCE_COLOR[value],
        whiteSpace: "nowrap",
      }}
    >
      {EVIDENCE_LABEL[value]}
    </span>
  );
}

export function AssumptionLedger({
  ledger,
  provenance,
  open = false,
}: {
  ledger: Ledger | undefined;
  provenance?: ProvenanceSummary;
  open?: boolean;
}) {
  if (!ledger) return null;
  return (
    <details className="panel" open={open} style={{ padding: 10 }}>
      <summary style={{ cursor: "pointer", listStyle: "none" }}>
        <span className="row" style={{ gap: 8 }}>
          <h3 style={{ margin: 0 }}>Why does Gotham think this?</h3>
          <EvidenceChip value={ledger.standing} />
        </span>
      </summary>
      <div className="dim" style={{ fontSize: 12, margin: "6px 0 8px", lineHeight: 1.5 }}>
        {ledger.claim}. The claim is only as firm as the weakest thing under it,
        which is <strong>{EVIDENCE_LABEL[ledger.standing]}</strong>.
      </div>
      <div className="col" style={{ gap: 6 }}>
        {ledger.assumptions.map((a, i) => (
          <div key={i} style={{ borderTop: "1px solid var(--border)", paddingTop: 5 }}>
            <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
              <span className="mono" style={{ fontSize: 12, color: "var(--text-dim)" }}>
                {a.subject}
              </span>
              <EvidenceChip value={a.evidence} />
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.45 }}>{a.statement}</div>
            <div className="dim mono" style={{ fontSize: 11 }}>source: {a.source}</div>
          </div>
        ))}
      </div>
      {provenance && (
        <div
          className="dim"
          style={{ fontSize: 12, marginTop: 8, paddingTop: 6, borderTop: "1px solid var(--border)" }}
        >
          {provenance.sentence}.
        </div>
      )}
    </details>
  );
}
