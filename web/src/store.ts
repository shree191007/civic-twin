/** The single application store (spec 05 section 3). */
import { create } from "zustand";
import type {
  ObjectiveMode,
  PlanId,
  Portfolio,
  ScenarioRequest,
  ScenarioResponse,
} from "./api/types";
import { post } from "./api/client";

export type AnalysisMode =
  | "functionality"
  | "state"
  | "flood"
  | "criticality"
  | "provenance"
  | "risk";
export type Speed = 0.25 | 0.5 | 1 | 2 | 4;

export const HORIZON_H = 72;
export const ALL_PORTFOLIOS: Portfolio[] = [
  "transport",
  "water",
  "energy",
  "comms",
  "services",
];

export interface CameraTarget {
  lon: number;
  lat: number;
  zoom: number;
  pitch: number;
  bearing?: number;
}

interface AppState {
  scenarioId: string;
  plan: PlanId;
  t: number;
  playing: boolean;
  speed: Speed;

  mode: AnalysisMode;
  activeLayers: Set<Portfolio>;
  selectedAsset: string | null;
  hoveredAsset: string | null;
  compareWith: string | null;
  cameraTarget: CameraTarget | null;
  savedCamera: CameraTarget | null;

  budget: number;
  objective: ObjectiveMode;
  failMode: boolean;
  forcedFailures: string[];
  adhoc: ScenarioResponse | null;
  adhocPending: boolean;
  /** What was asked for, so the same storm can be re-run under another plan. */
  adhocRequest: ScenarioRequest | null;
  /** What each named plan actually buys, learned from the precomputed runs.
   *  Needed so an ad-hoc storm can be re-run under a different plan. */
  planInterventions: Partial<Record<PlanId, string[]>>;
  error: string | null;
  copilotOpen: boolean;
  fallback2d: boolean;
  forceHighDetail: boolean;

  setT: (t: number) => void;
  stepT: (dt: number) => void;
  setPlaying: (v: boolean) => void;
  togglePlaying: () => void;
  setSpeed: (s: Speed) => void;
  setScenario: (id: string) => void;
  setPlan: (p: PlanId) => void;
  setMode: (m: AnalysisMode) => void;
  toggleLayer: (p: Portfolio) => void;
  selectAsset: (id: string | null) => void;
  hoverAsset: (id: string | null) => void;
  setCompareWith: (v: string | null) => void;
  flyTo: (target: CameraTarget) => void;
  saveCamera: (c: CameraTarget) => void;
  restoreCamera: () => void;
  setBudget: (b: number) => void;
  setObjective: (o: ObjectiveMode) => void;
  setFailMode: (v: boolean) => void;
  toggleForcedFailure: (assetId: string) => void;
  clearForcedFailures: () => void;
  runAdhoc: (req: ScenarioRequest) => Promise<void>;
  setCopilotOpen: (v: boolean) => void;
  setError: (v: string | null) => void;
  setPlanInterventions: (plan: PlanId, interventions: string[]) => void;
  setFallback2d: (v: boolean) => void;
  setForceHighDetail: (v: boolean) => void;
}

export const useStore = create<AppState>((set, get) => ({
  scenarioId: "storm_50y",
  plan: "baseline",
  t: 0,
  playing: false,
  speed: 1,

  mode: "functionality",
  activeLayers: new Set(ALL_PORTFOLIOS),
  selectedAsset: null,
  hoveredAsset: null,
  compareWith: null,
  cameraTarget: null,
  savedCamera: null,

  budget: 30_000_000,
  objective: "balanced",
  failMode: false,
  forcedFailures: [],
  adhoc: null,
  adhocPending: false,
  adhocRequest: null,
  planInterventions: {},
  error: null,
  copilotOpen: false,
  fallback2d: false,
  forceHighDetail: false,

  setT: (t) => set({ t: Math.max(0, Math.min(HORIZON_H, t)) }),
  stepT: (dt) => {
    const next = get().t + dt;
    if (next >= HORIZON_H) set({ t: HORIZON_H, playing: false });
    else set({ t: next });
  },
  setPlaying: (playing) => set({ playing }),
  togglePlaying: () => {
    const { playing, t } = get();
    if (!playing && t >= HORIZON_H) set({ t: 0, playing: true });
    else set({ playing: !playing });
  },
  setSpeed: (speed) => set({ speed }),
  setScenario: (scenarioId) => set({ scenarioId, t: 0, playing: false, adhoc: null }),
  setPlan: (plan) => {
    set({ plan });
    // An ad-hoc run is tied to the interventions it was run with. Leaving the
    // old result on screen under a new plan label would show one plan's map
    // with another plan's name on it, so re-run the same storm.
    const { scenarioId, adhocRequest, runAdhoc } = get();
    if (isAdhoc(scenarioId) && adhocRequest) {
      void runAdhoc({ ...adhocRequest, plan });
    }
  },
  setMode: (mode) => set({ mode }),
  toggleLayer: (p) =>
    set((s) => {
      const next = new Set(s.activeLayers);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return { activeLayers: next };
    }),
  selectAsset: (selectedAsset) => set({ selectedAsset }),
  hoverAsset: (hoveredAsset) => set({ hoveredAsset }),
  setCompareWith: (compareWith) => set({ compareWith }),
  flyTo: (cameraTarget) => set({ cameraTarget }),
  saveCamera: (savedCamera) => set({ savedCamera }),
  restoreCamera: () => {
    const saved = get().savedCamera;
    if (saved) set({ cameraTarget: saved });
  },
  setBudget: (budget) => set({ budget }),
  setObjective: (objective) => set({ objective }),
  setFailMode: (failMode) => set({ failMode }),
  toggleForcedFailure: (assetId) =>
    set((s) => ({
      forcedFailures: s.forcedFailures.includes(assetId)
        ? s.forcedFailures.filter((a) => a !== assetId)
        : [...s.forcedFailures, assetId],
    })),
  clearForcedFailures: () => set({ forcedFailures: [], adhoc: null }),

  runAdhoc: async (req) => {
    set({ adhocPending: true, error: null, adhocRequest: req });
    try {
      const plan = req.plan ?? get().plan;
      const response = await post<ScenarioResponse>("/scenarios", {
        record: true,
        rain_mm: req.rain_mm,
        field_seed: req.field_seed,
        onset_hour: req.onset_hour,
        forced_failures: req.forced_failures ?? [],
        interventions: req.interventions ?? get().planInterventions[plan] ?? [],
      });
      set({
        adhoc: response,
        scenarioId: `adhoc:${response.scenario_id}`,
        adhocPending: false,
      });
    } catch (e) {
      // Swallowing this left the map showing a stale scenario with no hint
      // that the new one had failed.
      set({
        adhocPending: false,
        error:
          e instanceof Error
            ? `Could not run that scenario: ${e.message}`
            : "Could not run that scenario.",
      });
    }
  },

  setError: (error) => set({ error }),

  setPlanInterventions: (plan, interventions) =>
    set((s) => ({
      planInterventions: { ...s.planInterventions, [plan]: interventions },
    })),

  setCopilotOpen: (copilotOpen) => set({ copilotOpen }),
  setFallback2d: (fallback2d) =>
    set((s) => (s.forceHighDetail ? s : { ...s, fallback2d })),
  setForceHighDetail: (forceHighDetail) =>
    set(forceHighDetail ? { forceHighDetail, fallback2d: false } : { forceHighDetail }),
}));

export const isAdhoc = (scenarioId: string): boolean => scenarioId.startsWith("adhoc:");

