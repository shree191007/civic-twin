/** Number and unit formatting. Every user-visible number goes through here. */

const NBSP = " ";

export function people(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString("en-IN");
}

export function hours(h: number | null | undefined): string {
  if (h == null || !Number.isFinite(h)) return "never";
  if (h < 1) return `${Math.round(h * 60)}${NBSP}min`;
  return `${h % 1 === 0 ? h : h.toFixed(1)}${NBSP}h`;
}

/** Indian money conventions: lakh up to a crore, then crore. */
export function inr(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(n % 1e7 === 0 ? 0 : 2)}${NBSP}crore`;
  if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(n % 1e5 === 0 ? 0 : 1)}${NBSP}lakh`;
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
}

export function personHours(ph: number | null | undefined): string {
  if (ph == null || !Number.isFinite(ph)) return "—";
  if (Math.abs(ph) >= 1e6) return `${(ph / 1e6).toFixed(2)}${NBSP}M person-hours`;
  if (Math.abs(ph) >= 1e3) return `${(ph / 1e3).toFixed(1)}${NBSP}k person-hours`;
  return `${Math.round(ph)}${NBSP}person-hours`;
}

export function percent(x: number | null | undefined, digits = 0): string {
  if (x == null || !Number.isFinite(x)) return "—";
  return `${(x * 100).toFixed(digits)}%`;
}

export function signed(n: number, fmt: (v: number) => string): string {
  return `${n > 0 ? "+" : ""}${fmt(n)}`;
}

export function returnPeriod(years: number | null | undefined): string {
  if (years == null || !Number.isFinite(years)) return "—";
  return `1-in-${Math.round(years)}-year`;
}
