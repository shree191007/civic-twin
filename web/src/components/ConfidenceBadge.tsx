import { percent } from "../lib/format";

/** Ensemble selection frequency, drawn as a filled ring plus the number. */
export function ConfidenceBadge({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="dim mono" style={{ fontSize: 10 }}>—</span>;
  const r = 6;
  const circumference = 2 * Math.PI * r;
  return (
    <span className="row" style={{ gap: 4 }} title={`Chosen in ${percent(value)} of ensemble members`}>
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r={r} fill="none" stroke="var(--border)" strokeWidth="2.5" />
        <circle
          cx="8"
          cy="8"
          r={r}
          fill="none"
          stroke="var(--f-full)"
          strokeWidth="2.5"
          strokeDasharray={`${circumference * value} ${circumference}`}
          transform="rotate(-90 8 8)"
          strokeLinecap="round"
        />
      </svg>
      <span className="mono" style={{ fontSize: 10 }}>{percent(value)}</span>
    </span>
  );
}
