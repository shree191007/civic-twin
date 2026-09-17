/** React Query hooks. All server state lives here, never in the store. */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { get } from "./client";
import type {
  AssetDetail,
  AssumptionsResponse,
  ObjectivesResponse,
  RedundancyResponse,
  BaselinesResponse,
  CompareResponse,
  Criticality,
  DecisionRecord,
  FrontierResponse,
  Health,
  PlansResponse,
  PrecomputedScenario,
  RestorationResponse,
  RiskResponse,
  ScenarioResponse,
  Spof,
  TownshipResponse,
  TraceResponse,
  Versioned,
} from "./types";

const STATIC = { staleTime: Infinity, gcTime: Infinity, retry: 1 };

export const useHealth = (): UseQueryResult<Health> =>
  useQuery({ queryKey: ["health"], queryFn: () => get<Health>("/health"), retry: 1 });

export const useTownship = (): UseQueryResult<TownshipResponse> =>
  useQuery({ queryKey: ["township"], queryFn: () => get<TownshipResponse>("/township"), ...STATIC });

export const useRisk = (): UseQueryResult<RiskResponse> =>
  useQuery({ queryKey: ["risk"], queryFn: () => get<RiskResponse>("/risk"), ...STATIC });

export const useCriticality = (): UseQueryResult<{ count: number; items: Criticality[] } & Versioned> =>
  useQuery({
    queryKey: ["criticality"],
    queryFn: () => get<{ count: number; items: Criticality[] } & Versioned>("/criticality?limit=200"),
    ...STATIC,
  });

export const useSpofs = (): UseQueryResult<{ count: number; items: Spof[] } & Versioned> =>
  useQuery({
    queryKey: ["spofs"],
    queryFn: () => get<{ count: number; items: Spof[] } & Versioned>("/spofs"),
    ...STATIC,
  });

export const useFrontier = (): UseQueryResult<FrontierResponse> =>
  useQuery({ queryKey: ["frontier"], queryFn: () => get<FrontierResponse>("/frontier"), ...STATIC });

export const useBaselines = (): UseQueryResult<BaselinesResponse> =>
  useQuery({ queryKey: ["baselines"], queryFn: () => get<BaselinesResponse>("/baselines"), ...STATIC });

export const usePrecomputed = (): UseQueryResult<PrecomputedScenario[]> =>
  useQuery({
    queryKey: ["precomputed"],
    queryFn: () => get<PrecomputedScenario[]>("/scenarios/precomputed"),
    ...STATIC,
  });

export const useHero = (scenarioId: string, plan: string, enabled = true): UseQueryResult<ScenarioResponse> =>
  useQuery({
    queryKey: ["hero", scenarioId, plan],
    queryFn: () => get<ScenarioResponse>(`/scenarios/${scenarioId}?plan=${plan}`),
    enabled: enabled && !scenarioId.startsWith("adhoc:"),
    ...STATIC,
  });

export const useAsset = (id: string | null): UseQueryResult<AssetDetail> =>
  useQuery({
    queryKey: ["asset", id],
    queryFn: () => get<AssetDetail>(`/assets/${id}`),
    enabled: Boolean(id),
    ...STATIC,
  });

export const useTrace = (id: string | null, direction: "up" | "down" = "down"): UseQueryResult<TraceResponse> =>
  useQuery({
    queryKey: ["trace", id, direction],
    queryFn: () => get<TraceResponse>(`/assets/${id}/trace?direction=${direction}`),
    enabled: Boolean(id),
    ...STATIC,
  });

export const usePlans = (budget: number): UseQueryResult<PlansResponse> =>
  useQuery({
    queryKey: ["plans", budget],
    queryFn: () => get<PlansResponse>(`/plans?budget=${Math.round(budget)}`),
    staleTime: Infinity,
    retry: 1,
  });

export const useCompare = (a: string, b: string, enabled = true): UseQueryResult<CompareResponse> =>
  useQuery({
    queryKey: ["compare", a, b],
    queryFn: () => get<CompareResponse>(`/scenarios/compare?a=${a}&b=${b}`),
    enabled,
    ...STATIC,
  });

export const useDecisions = (): UseQueryResult<{ count: number; items: DecisionRecord[] } & Versioned> =>
  useQuery({
    queryKey: ["decisions"],
    queryFn: () => get<{ count: number; items: DecisionRecord[] } & Versioned>("/decisions"),
    retry: 1,
  });

export const useRedundancy = (): UseQueryResult<RedundancyResponse> =>
  useQuery({
    queryKey: ["redundancy"],
    queryFn: () => get<RedundancyResponse>("/redundancy"),
    ...STATIC,
  });

export const useObjectives = (): UseQueryResult<ObjectivesResponse> =>
  useQuery({
    queryKey: ["objectives"],
    queryFn: () => get<ObjectivesResponse>("/objectives"),
    ...STATIC,
  });

export const useAssumptions = (
  claim: string,
  asset?: string | null,
): UseQueryResult<AssumptionsResponse> =>
  useQuery({
    queryKey: ["assumptions", claim, asset ?? null],
    queryFn: () =>
      get<AssumptionsResponse>(
        asset
          ? `/assumptions?asset=${encodeURIComponent(asset)}`
          : `/assumptions?claim=${encodeURIComponent(claim)}`,
      ),
    ...STATIC,
  });

export const useRestoration = (scenarioId: string): UseQueryResult<RestorationResponse> =>
  useQuery({
    queryKey: ["restoration", scenarioId],
    queryFn: () => get<RestorationResponse>(`/restoration/${scenarioId}`),
    enabled: !scenarioId.startsWith("adhoc:"),
    ...STATIC,
  });
