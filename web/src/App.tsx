/** App shell: the icon rail, routes, keyboard shortcuts, and the copilot drawer. */
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
  { path: "/explore", label: "Explore", key: "1", glyph: "◫" },
  { path: "/scenario", label: "Scenario", key: "2", glyph: "◷" },
  { path: "/risk", label: "Risk", key: "3", glyph: "◭" },
  { path: "/plan", label: "Plan", key: "4", glyph: "◧" },
  { path: "/log", label: "Log", key: "5", glyph: "☰" },
];

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
    <div style={{ display: "grid", gridTemplateColumns: "var(--rail) 1fr", height: "100%" }}>
      <nav
        className="rail"
        style={{
          background: "var(--bg-panel)",
          borderRight: "1px solid var(--border)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          padding: "8px 0",
          gap: 4,
        }}
      >
        {ROUTES.map((r) => (
          <NavLink
            key={r.path}
            to={r.path}
            title={`${r.label} (${r.key})`}
            style={({ isActive }) => ({
              width: 34,
              height: 34,
              display: "grid",
              placeItems: "center",
              borderRadius: 3,
              fontSize: 15,
              textDecoration: "none",
              color: isActive ? "var(--text)" : "var(--text-dim)",
              background: isActive ? "var(--bg-elevated)" : "transparent",
              border: `1px solid ${isActive ? "var(--border)" : "transparent"}`,
            })}
          >
            {r.glyph}
          </NavLink>
        ))}
        <div className="spacer" />
        <button
          onClick={() => setCopilotOpen(!copilotOpen)}
          title="Copilot (C)"
          aria-pressed={copilotOpen}
          style={{ width: 34, height: 34, padding: 0 }}
        >
          ✳
        </button>
      </nav>

      <div style={{ position: "relative", minWidth: 0 }}>
        <header
          className="row"
          style={{
            height: 30,
            padding: "0 10px",
            borderBottom: "1px solid var(--border)",
            background: "var(--bg-panel)",
            gap: 12,
          }}
        >
          <div className="row" style={{ gap: 3 }} role="group" aria-label="Analysis mode">
            {Object.entries(MODE_KEYS).map(([key, m]) => (
              <button
                key={m}
                aria-pressed={mode === m}
                onClick={() => setMode(m)}
                title={`${m} (${key.toUpperCase()})`}
                style={{ fontSize: 10, padding: "2px 7px", transition: "background 200ms" }}
              >
                {m}
              </button>
            ))}
          </div>
          <span style={{ width: 1, height: 16, background: "var(--border)" }} />
          <div className="row" style={{ gap: 3 }}>
            {ALL_PORTFOLIOS.map((p) => (
              <button
                key={p}
                aria-pressed={activeLayers.has(p)}
                onClick={() => toggleLayer(p)}
                className="mono"
                style={{
                  fontSize: 10,
                  padding: "2px 7px",
                  borderLeft: `3px solid ${css(PORTFOLIO_COLOR[p])}`,
                  opacity: activeLayers.has(p) ? 1 : 0.45,
                }}
              >
                {p}
              </button>
            ))}
          </div>
          <span className="spacer" />
          {flat && (
            <button
              className="mono"
              style={{ fontSize: 9, padding: "1px 6px" }}
              onClick={() => setForceHighDetail(true)}
              title="This machine is rendering below 15 fps. Click to force the 3D view anyway."
            >
              2D fallback — low frame rate · force 3D
            </button>
          )}
          {fixtures && <span className="mono" style={{ fontSize: 9, color: "var(--f-degraded)" }}>fixture data</span>}
          {health && (
            <span className="mono dim" style={{ fontSize: 9 }}>
              {health.role} · {health.status}
            </span>
          )}
        </header>

        <main style={{ position: "absolute", top: 30, left: 0, right: 0, bottom: 0 }}>
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
