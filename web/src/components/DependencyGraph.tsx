/** Cytoscape view of a dependency trace, dagre layout, left to right. */
import { useEffect, useRef } from "react";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import type { TraceResponse } from "../api/types";
import { css, PORTFOLIO_COLOR } from "../lib/colors";
import { useStore } from "../store";

cytoscape.use(dagre);

export function DependencyGraph({ trace }: { trace: TraceResponse | null | undefined }) {
  const container = useRef<HTMLDivElement>(null);
  const selectAsset = useStore((s) => s.selectAsset);

  useEffect(() => {
    if (!container.current || !trace || trace.nodes.length === 0) return;
    const cy = cytoscape({
      container: container.current,
      elements: [
        ...trace.nodes.map((n) => ({
          data: { id: n.id, label: n.id, portfolio: n.portfolio, root: n.is_root ? 1 : 0 },
        })),
        ...trace.edges.map((e) => ({
          data: { id: `${e.source}>${e.target}`, source: e.source, target: e.target, kind: e.kind },
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": (n: cytoscape.NodeSingular) =>
              css(PORTFOLIO_COLOR[n.data("portfolio") as string] ?? PORTFOLIO_COLOR.transport),
            label: "data(label)",
            color: "#0f172a",
            "font-size": 11,
            "font-family": "JetBrains Mono, ui-monospace, monospace",
            "text-valign": "center",
            "text-halign": "right",
            "text-margin-x": 4,
            width: 11,
            height: 11,
          },
        },
        {
          selector: "node[root = 1]",
          style: { "border-width": 2, "border-color": "#0f172a", width: 15, height: 15 },
        },
        {
          selector: "edge",
          style: {
            width: 1.2,
            "line-color": "#94a3b8",
            "target-arrow-color": "#94a3b8",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.6,
            "curve-style": "bezier",
          },
        },
      ],
      layout: { name: "dagre", rankDir: "LR", nodeSep: 14, rankSep: 46 } as cytoscape.LayoutOptions,
      userZoomingEnabled: false,
      autoungrabify: true,
    });
    cy.on("tap", "node", (event) => selectAsset(event.target.id() as string));
    return () => cy.destroy();
  }, [trace, selectAsset]);

  if (!trace || trace.nodes.length <= 1) {
    return (
      <div className="dim" style={{ fontSize: 13, padding: 8 }}>
        No further dependencies in this direction.
      </div>
    );
  }
  return <div ref={container} style={{ height: 190, width: "100%" }} />;
}
