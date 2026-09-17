/** The scenario player: map, state plot, drawdown charts, transport controls. */
import { useEffect, useMemo, useRef, useState } from "react";
import { DeckMap } from "../../map/DeckMap";
import { Hud } from "../../components/Hud";
import { Legend } from "../../components/Legend";
import { PortfolioTimeline } from "../../components/PortfolioTimeline";
import { ForecastPanel } from "../../components/ForecastPanel";
import { DrawdownCharts } from "../../components/DrawdownCharts";
import { TimelineScrubber } from "../../components/TimelineScrubber";
import { useCriticality, useHero, usePrecomputed, useRisk, useTownship } from "../../api/queries";
import { useScenario } from "../../lib/useScenario";
import { isAdhoc, useStore } from "../../store";
import { legendFor } from "../../map/modes";
import { returnPeriod } from "../../lib/format";
import type { PlanId } from "../../api/types";

const PLANS: PlanId[] = ["baseline", "asset_by_asset", "optimised"];
const DEBOUNCE_MS = 400;

export function Scenario() {
  const { data: township } = useTownship();
  const { data: criticality } = useCriticality();
  const { data: risk } = useRisk();
  const { data: precomputed } = usePrecomputed();
  const { timeline, frame, loading } = useScenario();

  const scenarioId = useStore((s) => s.scenarioId);
  const setScenario = useStore((s) => s.setScenario);
  const plan = useStore((s) => s.plan);
  const setPlan = useStore((s) => s.setPlan);
  const mode = useStore((s) => s.mode);
  const failMode = useStore((s) => s.failMode);
  const setFailMode = useStore((s) => s.setFailMode);
  const forced = useStore((s) => s.forcedFailures);
  const clearForced = useStore((s) => s.clearForcedFailures);
  const runAdhoc = useStore((s) => s.runAdhoc);
  const adhocPending = useStore((s) => s.adhocPending);
  const error = useStore((s) => s.error);
  const setError = useStore((s) => s.setError);
  const setPlanInterventions = useStore((s) => s.setPlanInterventions);

  const storms = useMemo(
    () => [...(precomputed ?? [])].sort((a, b) => a.rain_mm - b.rain_mm),
    [precomputed],
  );
  const [rain, setRain] = useState<number | null>(null);
  const [forecastMode, setForecastMode] = useState(false);
  const effectiveRain =
    rain ?? storms.find((s) => s.scenario_id === scenarioId)?.rain_mm ?? storms[0]?.rain_mm ?? 150;

  const baseline = useHero(isAdhoc(scenarioId) ? "" : scenarioId, "baseline", plan !== "baseline");
  const activePlanRun = useHero(isAdhoc(scenarioId) ? "" : scenarioId, plan);

  // Remember what each plan buys, so an ad-hoc storm can be re-run under it.
  useEffect(() => {
    if (activePlanRun.data?.interventions) {
      setPlanInterventions(plan, activePlanRun.data.interventions);
    }
  }, [activePlanRun.data, plan, setPlanInterventions]);

  // Dragging beyond the precomputed storms runs a live scenario, debounced.
  const timer = useRef<number | null>(null);
  useEffect(() => {
    if (rain == null) return;
    const snapped = storms.find((s) => Math.abs(s.rain_mm - rain) < 4);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (snapped) setScenario(snapped.scenario_id);
      else void runAdhoc({ rain_mm: rain, forced_failures: forced, record: true });
    }, DEBOUNCE_MS);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [rain, storms, forced, runAdhoc, setScenario]);

  useEffect(() => {
    if (forced.length > 0) void runAdhoc({ rain_mm: effectiveRain, forced_failures: forced, record: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forced]);

  const items = criticality?.items ?? [];
  const legend = legendFor(mode, Math.max(0, ...items.map((c) => c.tail_criticality_ph)));
  const kindOf = useMemo(() => {
    const index = new Map<string, string>();
    if (township) {
      for (const c of Object.values(township.layers)) {
        for (const f of c.features) index.set(f.properties.id, f.properties.kind);
      }
    }
    return (id: string) => index.get(id);
  }, [township]);

  if (!township) return <div className="dim" style={{ padding: 20 }}>Loading…</div>;

  const current = storms.find((s) => s.scenario_id === scenarioId);

  return (
    <div style={{ display: "grid", gridTemplateRows: "1fr auto", height: "100%", gap: 8, padding: 8 }}>
      <div style={{ display: "grid", gridTemplateColumns: "3fr 2fr", gap: 8, minHeight: 0 }}>
        <div style={{ position: "relative", overflow: "hidden", borderRadius: 3 }}>
          <DeckMap
            township={township}
            frame={frame}
            criticality={items}
            zoneCvar={risk?.zone_contributions_ph}
          />
          <Hud township={township} frame={frame} scenarioId={scenarioId} plan={plan} kindOf={kindOf} />
          <div style={{ position: "absolute", bottom: 10, left: 10 }}>
            <Legend spec={legend} />
          </div>
          {(loading || adhocPending) && (
            <div className="panel mono" style={{ position: "absolute", top: 10, right: 10, padding: "4px 8px", fontSize: 10 }}>
              computing…
            </div>
          )}
          {error && (
            <div
              className="panel row"
              role="alert"
              style={{
                position: "absolute",
                top: 10,
                right: 10,
                padding: "5px 9px",
                fontSize: 11,
                gap: 10,
                borderColor: "var(--f-down)",
              }}
            >
              <span>{error}</span>
              <button onClick={() => setError(null)} aria-label="Dismiss">×</button>
            </div>
          )}
        </div>
        <div className="col scroll" style={{ minHeight: 0 }}>
          <div className="row" style={{ gap: 4 }}>
            <button aria-pressed={!forecastMode} onClick={() => setForecastMode(false)} style={{ fontSize: 11 }}>
              Timeline
            </button>
            <button aria-pressed={forecastMode} onClick={() => setForecastMode(true)} style={{ fontSize: 11 }}>
              Forecast to impact
            </button>
          </div>
          {forecastMode ? (
            <ForecastPanel />
          ) : (
            <>
              <PortfolioTimeline township={township} timeline={timeline} criticality={items} />
              <DrawdownCharts
                timeline={timeline}
                baseline={plan !== "baseline" ? baseline.data?.result?.timeline : undefined}
              />
            </>
          )}
        </div>
      </div>

      <div className="panel" style={{ padding: 10 }}>
        <div className="row" style={{ gap: 18, marginBottom: 8 }}>
          <div className="row" style={{ gap: 6, flex: 1 }}>
            <span className="dim mono" style={{ fontSize: 10, whiteSpace: "nowrap" }}>
              rainfall {Math.round(effectiveRain)} mm
              {current ? ` · ${returnPeriod(current.return_period_y)}` : " · ad hoc"}
            </span>
            <input
              type="range"
              min={Math.max(40, (storms[0]?.rain_mm ?? 100) - 40)}
              max={(storms.at(-1)?.rain_mm ?? 260) + 60}
              step={1}
              value={effectiveRain}
              onChange={(e) => setRain(Number(e.target.value))}
              aria-label="Rainfall in millimetres"
              style={{ flex: 1 }}
            />
          </div>
          <div className="row" style={{ gap: 4 }}>
            {PLANS.map((p) => (
              <button key={p} aria-pressed={plan === p} onClick={() => setPlan(p)} style={{ fontSize: 11 }}>
                {p.replace(/_/g, " ")}
              </button>
            ))}
          </div>
          <div className="row" style={{ gap: 4 }}>
            <button aria-pressed={failMode} onClick={() => setFailMode(!failMode)} style={{ fontSize: 11 }}>
              Fail an asset
            </button>
            {forced.length > 0 && (
              <button onClick={clearForced} className="mono" style={{ fontSize: 10 }}>
                clear {forced.length}
              </button>
            )}
          </div>
        </div>
        <TimelineScrubber />
      </div>
    </div>
  );
}
