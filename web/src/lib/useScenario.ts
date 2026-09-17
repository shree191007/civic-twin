/** Resolves the current scenario (precomputed or ad-hoc) into a timeline. */
import { useMemo } from "react";
import { useHero } from "../api/queries";
import { isAdhoc, useStore } from "../store";
import { frameAt, EMPTY_FRAME, type Frame } from "./frames";
import type { ScenarioResponse } from "../api/types";

export interface ScenarioState {
  response: ScenarioResponse | undefined;
  timeline: Frame[];
  frame: Frame;
  loading: boolean;
}

export function useScenario(planOverride?: string): ScenarioState {
  const scenarioId = useStore((s) => s.scenarioId);
  const plan = useStore((s) => s.plan);
  const t = useStore((s) => s.t);
  const adhoc = useStore((s) => s.adhoc);
  const adhocPending = useStore((s) => s.adhocPending);

  const usingAdhoc = isAdhoc(scenarioId);
  const hero = useHero(scenarioId, planOverride ?? plan, !usingAdhoc);
  const response = usingAdhoc ? adhoc ?? undefined : hero.data;
  const timeline = useMemo(() => response?.result?.timeline ?? [], [response]);

  return {
    response,
    timeline,
    frame: timeline.length ? frameAt(timeline, t) : EMPTY_FRAME,
    loading: usingAdhoc ? adhocPending : hero.isLoading,
  };
}
