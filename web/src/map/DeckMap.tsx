/** The shared map shell: basemap, view state, layer assembly, fps guard. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DeckGL from "@deck.gl/react";
import { Map as MapLibre } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Layer } from "@deck.gl/core";
import { buildLayers, type LayerOptions } from "./layers";
import { applyTarget, initialViewState, type ViewStateLike } from "./camera";
import { useStore } from "../store";
import type { Criticality, TownshipResponse, TraceResponse } from "../api/types";
import type { Frame } from "../lib/frames";
import type { ModeContext } from "./modes";

/** CARTO Positron: a light basemap that needs no API key. */
const BASEMAP = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

const LOW_FPS = 15;
const LOW_FPS_SECONDS = 3;
/** Below this many redraws in the window the view is idle, not struggling. */
const MIN_RENDERS_TO_JUDGE = LOW_FPS_SECONDS * 5;
/** How often the travelling pulse is allowed to repaint the arc layer. */
const PULSE_REPAINT_MS = 90;

export interface DeckMapProps {
  township: TownshipResponse;
  frame: Frame;
  criticality: Criticality[];
  zoneCvar?: Record<string, number>;
  trace?: TraceResponse | null;
  /** Keeps two side-by-side maps on one view state. */
  sharedViewState?: ViewStateLike;
  onViewStateChange?: (v: ViewStateLike) => void;
  interactive?: boolean;
  children?: React.ReactNode;
}

export function DeckMap({
  township,
  frame,
  criticality,
  zoneCvar,
  trace,
  sharedViewState,
  onViewStateChange,
  interactive = true,
  children,
}: DeckMapProps) {
  const mode = useStore((s) => s.mode);
  const activeLayers = useStore((s) => s.activeLayers);
  const selectedAsset = useStore((s) => s.selectedAsset);
  const selectAsset = useStore((s) => s.selectAsset);
  const hoverAsset = useStore((s) => s.hoverAsset);
  const failMode = useStore((s) => s.failMode);
  const toggleForcedFailure = useStore((s) => s.toggleForcedFailure);
  const cameraTarget = useStore((s) => s.cameraTarget);
  const flat = useStore((s) => s.fallback2d);
  const setFallback2d = useStore((s) => s.setFallback2d);

  const [internalView, setInternalView] = useState<ViewStateLike>(() =>
    initialViewState(township.bbox),
  );
  const viewState = sharedViewState ?? internalView;

  // The travelling pulse along the traced arcs. Kept in a ref and read by the
  // layer's own accessors: driving it through React state re-ran this
  // component, and with it the whole layer list, sixty times a second.
  const dashOffset = useRef(0);
  const [pulseTick, setPulseTick] = useState(0);
  useEffect(() => {
    if (!trace) return;
    let raf = 0;
    let lastRepaint = 0;
    const tick = (now: number) => {
      dashOffset.current += 0.35;
      // Only nudge React when the arc colour would visibly change.
      if (now - lastRepaint > PULSE_REPAINT_MS) {
        lastRepaint = now;
        setPulseTick((v) => v + 1);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [trace]);

  // Drop to a flat 2D view only if the machine cannot keep up when it is being
  // driven by the user. Timeline playback advances the hour a few times a
  // second by design, and counting those as frames made a perfectly healthy
  // machine demote itself the moment somebody pressed play.
  const renderTimes = useRef<number[]>([]);
  const interacting = useRef(false);
  const onAfterRender = useCallback(() => {
    if (!interacting.current) {
      renderTimes.current = [];
      return;
    }
    const now = performance.now();
    const samples = renderTimes.current;
    samples.push(now);
    const cutoff = now - LOW_FPS_SECONDS * 1000;
    while (samples.length > 0 && samples[0] < cutoff) samples.shift();
    if (samples.length < MIN_RENDERS_TO_JUDGE) return;
    if (samples.length / LOW_FPS_SECONDS < LOW_FPS) {
      renderTimes.current = [];
      setFallback2d(true);
    }
  }, [setFallback2d]);

  useEffect(() => {
    if (!cameraTarget) return;
    const next = applyTarget(viewState, cameraTarget);
    if (sharedViewState) onViewStateChange?.(next);
    else setInternalView(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraTarget]);

  const ctx: ModeContext = useMemo(() => {
    const map = new Map(criticality.map((c) => [c.asset_id, c]));
    const zones = new Map(Object.entries(zoneCvar ?? {}));
    return {
      frame,
      criticality: map,
      maxCriticality: Math.max(0, ...criticality.map((c) => c.tail_criticality_ph)),
      zoneCvar: zones,
      maxZoneCvar: Math.max(0, ...zones.values()),
    };
  }, [criticality, zoneCvar, frame]);

  const traceIds = useMemo(
    () => new Set((trace?.nodes ?? []).map((n) => n.id)),
    [trace],
  );
  const traceEdges = useMemo(
    () => new Set((trace?.edges ?? []).map((e) => `${e.source}>${e.target}`)),
    [trace],
  );

  const onClick = useCallback(
    (assetId: string) => {
      if (failMode) toggleForcedFailure(assetId);
      else selectAsset(assetId);
    },
    [failMode, selectAsset, toggleForcedFailure],
  );

  const layers: Layer[] = useMemo(() => {
    const options: LayerOptions = {
      township,
      frame,
      mode,
      ctx,
      activeLayers,
      selectedAsset,
      traceIds,
      traceEdges,
      dashOffset,
      flat,
      onClick,
      onHover: hoverAsset,
    };
    return buildLayers(options);
    // `pulseTick` is throttled to PULSE_REPAINT_MS; `dashOffset` is a ref that
    // the layer accessors read, so it deliberately does not appear here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    township,
    frame,
    mode,
    ctx,
    activeLayers,
    selectedAsset,
    traceIds,
    traceEdges,
    pulseTick,
    flat,
    onClick,
    hoverAsset,
  ]);

  const effectiveView = flat ? { ...viewState, pitch: 0 } : viewState;

  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <DeckGL
        viewState={effectiveView}
        controller={interactive}
        layers={layers}
        onAfterRender={onAfterRender}
        onInteractionStateChange={(interactionState) => {
          const active = Boolean(
            interactionState.isDragging ||
              interactionState.isPanning ||
              interactionState.isZooming ||
              interactionState.isRotating,
          );
          interacting.current = active;
          if (!active) renderTimes.current = [];
        }}
        onViewStateChange={({ viewState: next }) => {
          const v = next as ViewStateLike;
          if (sharedViewState) onViewStateChange?.(v);
          else setInternalView(v);
        }}
        getCursor={({ isDragging, isHovering }) =>
          isDragging ? "grabbing" : isHovering ? "pointer" : "grab"
        }
      >
        <MapLibre mapStyle={BASEMAP} attributionControl={false} />
      </DeckGL>
      {children}
    </div>
  );
}
