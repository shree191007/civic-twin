/** The state plot: one row per asset, one column per hour, colour = functionality.
 *  Rendered to a single canvas — 75 assets x 72 hours is 5,400 cells. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Criticality, Portfolio, TownshipResponse } from "../api/types";
import type { Frame } from "../lib/frames";
import { functionalityColor, PORTFOLIO_COLOR, css } from "../lib/colors";
import { ALL_PORTFOLIOS, useStore } from "../store";
import { percent } from "../lib/format";

const ROW_H = 12;
const LABEL_W = 70;
const HEADER_H = 18;

interface Props {
  township: TownshipResponse;
  timeline: Frame[];
  criticality: Criticality[];
}

interface Row {
  id: string;
  portfolio: Portfolio;
  first: boolean;
}

export function PortfolioTimeline({ township, timeline, criticality }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const t = useStore((s) => s.t);
  const setT = useStore((s) => s.setT);
  const selectAsset = useStore((s) => s.selectAsset);
  const [tip, setTip] = useState<string | null>(null);

  const rows: Row[] = useMemo(() => {
    const rank = new Map(criticality.map((c) => [c.asset_id, c.tail_criticality_ph]));
    const out: Row[] = [];
    for (const portfolio of ALL_PORTFOLIOS) {
      const ids = (township.layers[portfolio]?.features ?? [])
        .map((f) => f.properties.id)
        .sort((a, b) => (rank.get(b) ?? 0) - (rank.get(a) ?? 0) || a.localeCompare(b));
      ids.forEach((id, i) => out.push({ id, portfolio, first: i === 0 }));
    }
    return out;
  }, [township, criticality]);

  const cols = timeline.length || 1;

  const draw = useCallback(() => {
    const el = canvas.current;
    if (!el) return;
    const width = el.clientWidth;
    const height = rows.length * ROW_H + HEADER_H;
    const dpr = window.devicePixelRatio || 1;
    el.width = width * dpr;
    el.height = height * dpr;
    el.style.height = `${height}px`;
    const g = el.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, width, height);

    const cellW = Math.max(1, (width - LABEL_W) / cols);

    g.font = '11px "JetBrains Mono", ui-monospace, monospace';
    g.textBaseline = "middle";

    rows.forEach((row, i) => {
      const y = HEADER_H + i * ROW_H;
      g.fillStyle = row.first ? css(PORTFOLIO_COLOR[row.portfolio]) : "#4b5a6d";
      g.fillText(row.id, 2, y + ROW_H / 2);
      for (let c = 0; c < cols; c++) {
        const frame = timeline[c];
        const f = frame ? frame.func[row.id] ?? 1 : 1;
        const color = functionalityColor(f);
        g.fillStyle = `rgb(${color[0]} ${color[1]} ${color[2]})`;
        g.fillRect(LABEL_W + c * cellW, y + 0.5, Math.max(1, cellW - 0.5), ROW_H - 1);
      }
    });

    // hour ruler
    g.fillStyle = "#4b5a6d";
    for (let h = 0; h <= cols; h += 6) {
      const x = LABEL_W + h * cellW;
      g.fillText(String(h), x, HEADER_H / 2);
    }

    // playhead
    const px = LABEL_W + Math.min(t, cols) * cellW;
    g.strokeStyle = "#0f172a";
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(px, 0);
    g.lineTo(px, height);
    g.stroke();
  }, [rows, cols, timeline, t]);

  useEffect(() => {
    draw();
    const observer = new ResizeObserver(draw);
    if (canvas.current) observer.observe(canvas.current);
    return () => observer.disconnect();
  }, [draw]);

  const locate = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const el = canvas.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const rowIndex = Math.floor((y - HEADER_H) / ROW_H);
    if (rowIndex < 0 || rowIndex >= rows.length || x < LABEL_W) return null;
    const cellW = (rect.width - LABEL_W) / cols;
    const hour = Math.max(0, Math.min(cols - 1, Math.floor((x - LABEL_W) / cellW)));
    return { row: rows[rowIndex], hour };
  };

  return (
    <div className="panel" style={{ padding: 6, overflow: "auto" }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
        <h3>Portfolio state · hours since landfall</h3>
        <span className="dim mono" style={{ fontSize: 11 }}>{tip ?? "hover a cell"}</span>
      </div>
      <canvas
        ref={canvas}
        style={{ width: "100%", display: "block", cursor: "crosshair" }}
        onMouseMove={(e) => {
          const hit = locate(e);
          if (!hit) return setTip(null);
          const frame = timeline[hit.hour];
          const f = frame?.func[hit.row.id] ?? 1;
          const reason = frame?.reason[hit.row.id];
          setTip(
            `${hit.row.id} · H+${hit.hour} · ${percent(f)}${reason ? ` · ${reason}` : ""}`,
          );
        }}
        onMouseLeave={() => setTip(null)}
        onClick={(e) => {
          const hit = locate(e);
          if (!hit) return;
          selectAsset(hit.row.id);
          setT(hit.hour);
        }}
      />
    </div>
  );
}
