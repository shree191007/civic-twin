/** App shell: named tabs, the map toolbar, routes, shortcuts, and the copilot drawer. */
import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Explore } from "./views/Explore";
import { Scenario } from "./views/Scenario";
import { Risk } from "./views/Risk";
import { Plan } from "./views/Plan";
import { Log } from "./views/Log";
import { CopilotDrawer } from "./components/CopilotDrawer";
import { ALL_PORTFOLIOS, useStore } from "./store";
import { MODE_KEYS } from "./map/modes";
import { onFixtureModeChange } from "./api/client";
import { css, PORTFOLIO_COLOR } from "./lib/colors";
import { useHealth } from "./api/queries";

const ROUTES = [
  { path: "/explore", label: "System map", key: "1", hint: "How the town's infrastructure depends on itself" },
  { path: "/scenario", label: "Storm playback", key: "2", hint: "Watch a flood cascade hour by hour" },
  { path: "/risk", label: "Risk & weak points", key: "3", hint: "Where the losses come from and the hidden single points of failure" },
  { path: "/plan", label: "Investment plan", key: "4", hint: "What to fund, and how much risk it removes" },
  { path: "/log", label: "Decisions & brief", key: "5", hint: "Record a decision and print the council brief" },
];

/** Plain-English names for what the map colours mean. */
const MODE_LABELS: Record<string, string> = {
  functionality: "Service level",
  state: "Operating state",
  flood: "Flood depth",
  criticality: "Criticality",
  provenance: "Data source",
  risk: "Zone risk",
};

const LAYER_LABELS: Record<string, string> = {
  transport: "Roads",
  water: "Water",
  energy: "Power",
  comms: "Telecom",
  services: "Hospitals & services",
};

export function App() {
  const mode = useStore((s) => s.mode);
  const setMode = useStore((s) => s.setMode);
  const activeLayers = useStore((s) => s.activeLayers);
  const toggleLayer = useStore((s) => s.toggleLayer);
  const togglePlaying = useStore((s) => s.togglePlaying);
  const copilotOpen = useStore((s) => s.copilotOpen);
  const setCopilotOpen = useStore((s) => s.setCopilotOpen);
  const restoreCamera = useStore((s) => s.restoreCamera);
  const flat = useStore((s) => s.fallback2d);
  const setForceHighDetail = useStore((s) => s.setForceHighDetail);
  const { data: health } = useHealth();
  const [fixtures, setFixtures] = useState(false);

  useEffect(() => onFixtureModeChange(setFixtures), []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      const key = event.key.toLowerCase();
      if (MODE_KEYS[key]) {
        setMode(MODE_KEYS[key]);
        return;
      }
      if (key === "c") setCopilotOpen(!copilotOpen);
      if (key === "g") restoreCamera();
      if (event.key === " ") {
        event.preventDefault();
        togglePlaying();
      }
      const route = ROUTES.find((r) => r.key === key);
      if (route) window.location.hash = `#${route.path}`;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [copilotOpen, restoreCamera, setCopilotOpen, setMode, togglePlaying]);


  return (
    <div className="app-root" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <header className="app-header">
        <div className="brand">
          <span className="brand-name">civic-twin</span>
          <span className="brand-sub">Infrastructure resilience</span>
        </div>
        <nav className="tabs" aria-label="Views">
          {ROUTES.map((r) => (
            <NavLink
              key={r.path}
              to={r.path}
              title={`${r.hint} (shortcut ${r.key})`}
              className={({ isActive }) => (isActive ? "tab tab-active" : "tab")}
            >
              {r.label}
            </NavLink>
          ))}
        </nav>
        <span className="spacer" />
        {health && (
          <span className="status-pill" title="API status and access role">
            <span className={health.status === "ok" ? "dot dot-ok" : "dot dot-warn"} />
            {health.status === "ok" ? "Connected" : "Degraded"} · {health.role}
          </span>
        )}
        <button
          className={copilotOpen ? "copilot-button copilot-open" : "copilot-button"}
          onClick={() => setCopilotOpen(!copilotOpen)}
          title="Ask questions about the model (shortcut C)"
          aria-pressed={copilotOpen}
        >
          Ask the copilot
        </button>
      </header>

      <div className="toolbar">
        <span className="toolbar-label">Colour map by</span>
        <div className="segmented" role="group" aria-label="Colour map by">
          {Object.entries(MODE_KEYS).map(([key, m]) => (
            <button
              key={m}
              aria-pressed={mode === m}
              onClick={() => setMode(m)}
              title={`Shortcut ${key.toUpperCase()}`}
            >
              {MODE_LABELS[m] ?? m}
            </button>
          ))}
        </div>
        <span className="toolbar-divider" />
        <span className="toolbar-label">Show layers</span>
        <div className="row" style={{ gap: 6 }} role="group" aria-label="Show layers">
          {ALL_PORTFOLIOS.map((p) => (
            <label key={p} className="layer-toggle">
              <input
                type="checkbox"
                checked={activeLayers.has(p)}
                onChange={() => toggleLayer(p)}
              />
              <span className="layer-swatch" style={{ background: css(PORTFOLIO_COLOR[p]) }} />
              {LAYER_LABELS[p] ?? p}
            </label>
          ))}
        </div>
        <span className="spacer" />
        {flat && (
          <button
            className="notice"
            onClick={() => setForceHighDetail(true)}
            title="This computer is drawing the map slowly, so it switched to a flat view."
          >
            Flat view (slow graphics) — switch to 3D
          </button>
        )}
        {fixtures && <span className="notice">Offline demo data</span>}
      </div>

      <div className="app-body" style={{ position: "relative", flex: 1, minHeight: 0 }}>
        <main style={{ position: "absolute", inset: 0 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/explore" replace />} />
            <Route path="/explore" element={<Explore />} />
            <Route path="/scenario" element={<Scenario />} />
            <Route path="/risk" element={<Risk />} />
            <Route path="/plan" element={<Plan />} />
            <Route path="/log" element={<Log />} />
          </Routes>
          <CopilotDrawer />
        </main>
      </div>
    </div>
  );
}
