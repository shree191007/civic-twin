/** A thin React wrapper around Observable Plot, with an empty state. */
import { useEffect, useRef } from "react";
import * as Plot from "@observablehq/plot";

export const PLOT_STYLE: Partial<CSSStyleDeclaration> = {
  background: "transparent",
  color: "#0f172a",
  fontFamily: '"JetBrains Mono", ui-monospace, monospace',
  fontSize: "12px",
};

export function PlotFigure({
  options,
  empty = "No data",
  height,
}: {
  options: Plot.PlotOptions | null;
  empty?: string;
  height?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = host.current;
    if (!el || !options) return;
    const extra: Partial<CSSStyleDeclaration> =
      typeof options.style === "object" && options.style ? options.style : {};
    const figure = Plot.plot({ ...options, style: { ...PLOT_STYLE, ...extra } });
    el.append(figure);
    return () => figure.remove();
  }, [options]);

  if (!options) {
    return (
      <div className="dim" style={{ fontSize: 13, padding: 10, height }}>
        {empty}
      </div>
    );
  }
  return <div ref={host} style={{ minHeight: height }} />;
}
