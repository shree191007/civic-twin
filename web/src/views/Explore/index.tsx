/** The system explorer: the layer cake, dependency tracing, and the inspector. */
import { useMemo } from "react";
import { DeckMap } from "../../map/DeckMap";
import { Hud } from "../../components/Hud";
import { Legend } from "../../components/Legend";
import { AssetInspector } from "../../components/AssetInspector";
import { TextAlternative } from "../../components/TextAlternative";
import { useCriticality, useRisk, useTownship } from "../../api/queries";
import { useScenario } from "../../lib/useScenario";
import { useStore } from "../../store";
import { legendFor } from "../../map/modes";
import { useTrace } from "../../api/queries";
import { frameTheCascade } from "../../map/camera";

export function Explore() {
  const { data: township } = useTownship();
  const { data: criticality } = useCriticality();
  const { data: risk } = useRisk();
  const { frame } = useScenario();
  const mode = useStore((s) => s.mode);
  const selected = useStore((s) => s.selectedAsset);
  const flyTo = useStore((s) => s.flyTo);
  const scenarioId = useStore((s) => s.scenarioId);
  const plan = useStore((s) => s.plan);
  const { data: trace } = useTrace(selected, "down");

  const kindOf = useMemo(() => {
    const index = new Map<string, string>();
    if (township) {
      for (const collection of Object.values(township.layers)) {
        for (const feature of collection.features) {
          index.set(feature.properties.id, feature.properties.kind);
        }
      }
    }
    return (id: string) => index.get(id);
  }, [township]);

  const items = criticality?.items ?? [];
  const legend = legendFor(mode, Math.max(0, ...items.map((c) => c.tail_criticality_ph)));

  if (!township) {
    return <div style={{ padding: 20 }} className="dim">Loading township…</div>;
  }

  return (
    <div className="stack-narrow" style={{ display: "grid", gridTemplateColumns: `1fr var(--panel)`, height: "100%", gap: 8, padding: 8 }}>
      <div style={{ position: "relative", overflow: "hidden", borderRadius: 3 }}>
        <DeckMap
          township={township}
          frame={frame}
          criticality={items}
          zoneCvar={risk?.zone_contributions_ph}
          trace={trace}
        />
        <Hud
          township={township}
          frame={frame}
          scenarioId={scenarioId}
          plan={plan}
          kindOf={kindOf}
        />
        <div style={{ position: "absolute", bottom: 10, left: 10 }}>
          <Legend spec={legend} />
        </div>
        <div style={{ position: "absolute", bottom: 10, right: 10, display: "flex", gap: 6 }}>
          <button onClick={() => flyTo(frameTheCascade(township.bbox))}>Frame the cascade</button>
        </div>
      </div>
      <div className="col" style={{ minHeight: 0 }}>
        <AssetInspector frame={frame} />
        <TextAlternative frame={frame} />
      </div>
    </div>
  );
}
