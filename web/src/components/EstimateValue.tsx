/** Renders a quantity as a range with its confidence (spec patch section 6).
 *
 *  Never a bare point estimate: the inputs to this model do not support the
 *  precision a single number implies.
 */
import type { Estimate } from "../api/types";
import { hours, people, percent, personHours } from "../lib/format";

type Formatter = (n: number) => string;

const FORMATTERS: Record<string, Formatter> = {
  people,
  "person-hours": personHours,
  hours,
  fraction: (n) => percent(n, 1),
};

const CONFIDENCE_COLOR: Record<string, string> = {
  high: "var(--f-full)",
  medium: "var(--f-degraded)",
  low: "var(--f-critical)",
};

/** People ranges read better rounded outward to something sayable. */
function roundOutward(value: number, up: boolean): number {
  const magnitude = Math.max(Math.abs(value), 1);
  const step = magnitude >= 100_000 ? 5_000 : magnitude >= 10_000 ? 1_000 : magnitude >= 1_000 ? 100 : 10;
  return (up ? Math.ceil(value / step) : Math.floor(value / step)) * step;
}

export function ConfidenceChip({ value }: { value: string }) {
  return (
    <span
      className="mono"
      title="How firm this figure is, judged from the width of its own range"
      style={{
        fontSize: 9,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        padding: "0 4px",
        borderRadius: 2,
        border: `1px solid ${CONFIDENCE_COLOR[value] ?? "var(--text-dim)"}`,
        color: CONFIDENCE_COLOR[value] ?? "var(--text-dim)",
      }}
    >
      {value}
    </span>
  );
}

export function EstimateValue({
  estimate,
  label,
  size = 15,
  round = false,
}: {
  estimate: Estimate | undefined;
  label?: string;
  size?: number;
  round?: boolean;
}) {
  if (!estimate) {
    return <span className="dim mono">—</span>;
  }
  const format = FORMATTERS[estimate.unit] ?? ((n: number) => n.toFixed(0));
  const low = round ? roundOutward(estimate.low, false) : estimate.low;
  const high = round ? roundOutward(estimate.high, true) : estimate.high;

  return (
    <div className="col" style={{ gap: 1 }}>
      <span className="mono" style={{ fontSize: size, lineHeight: 1.15 }}>
        {format(low)}
        <span className="dim"> – </span>
        {format(high)}
      </span>
      <div className="row" style={{ gap: 6 }}>
        {label && <span className="dim" style={{ fontSize: 9 }}>{label}</span>}
        <ConfidenceChip value={estimate.confidence} />
      </div>
    </div>
  );
}

/** The reasons a number is uncertain, named rather than implied. */
export function UncertaintyDrivers({ drivers }: { drivers: string[] }) {
  if (drivers.length === 0) return null;
  return (
    <div className="dim" style={{ fontSize: 10, lineHeight: 1.5 }}>
      Driven by: {drivers.map((d) => d.replace(/_/g, " ")).join(", ")}
    </div>
  );
}
