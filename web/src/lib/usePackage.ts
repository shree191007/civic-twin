/** The investment package for any budget, read off the greedy frontier.
 *
 *  Every prefix of the frontier is itself a plan, so dragging the budget
 *  shows exactly what the optimiser would buy with that money without a new
 *  optimisation run. Confidence scores come from the ensemble behind the one
 *  precomputed plan, and are only shown for measures that plan also chose.
 */
import { useMemo } from "react";
import { useFrontier, usePlans } from "../api/queries";
import type { PlanItem, PlanStep } from "../api/types";

export interface PackageItem {
  id: string;
  rank: number;
  label: string;
  kind: string;
  target: string | null;
  why: string;
  costInr: number;
  riskRemovedPh: number;
  confidence: number | null;
}

export interface Package {
  budgetInr: number;
  items: PackageItem[];
  costInr: number;
  cvarBeforePh: number;
  cvarAfterPh: number;
  reductionPct: number;
  /** Where the frontier stops finding anything worth buying. */
  saturationInr: number;
  /** Tail risk removed per crore on the last measure bought. */
  lastMarginalPerCrore: number;
  steps: PlanStep[];
  loading: boolean;
}

export const KIND_LABELS: Record<string, string> = {
  operational: "Operating rules",
  harden: "Flood-proofing",
  backup: "Backup power",
  fuel: "Fuel storage",
  storage: "Water storage",
  tie: "Feeder ties",
  reroute: "Cable rerouting",
  mobile: "Mobile generators",
  unknown: "Other",
};

export function usePackage(budgetInr: number): Package {
  const { data: frontier, isLoading: frontierLoading } = useFrontier();
  const { data: plans } = usePlans(budgetInr);

  return useMemo(() => {
    const steps = frontier?.steps ?? [];
    const confidence = new Map<string, number | null>(
      (plans?.interventions ?? []).map((i: PlanItem) => [i.id, i.selection_frequency]),
    );
    const before = plans?.plan.cvar_before ?? 0;

    const items: PackageItem[] = [];
    let previous = before;
    let previousCost = 0;
    for (const [index, step] of steps.entries()) {
      if (step.cumulative_cost_inr > budgetInr) break;
      items.push({
        id: step.intervention_id,
        rank: index + 1,
        label: step.label,
        kind: step.kind ?? "unknown",
        target: step.target_asset ?? null,
        why: step.why ?? "",
        costInr: step.cost_inr ?? step.cumulative_cost_inr - previousCost,
        riskRemovedPh: Math.max(0, previous - step.cvar_after),
        confidence: confidence.get(step.intervention_id) ?? null,
      });
      previous = step.cvar_after;
      previousCost = step.cumulative_cost_inr;
    }

    const last = items.at(-1);
    const lastStep = last ? steps[last.rank - 1] : undefined;
    const after = lastStep?.cvar_after ?? before;
    return {
      budgetInr,
      items,
      costInr: lastStep?.cumulative_cost_inr ?? 0,
      cvarBeforePh: before,
      cvarAfterPh: after,
      reductionPct: before > 0 ? (100 * (before - after)) / before : 0,
      saturationInr: steps.at(-1)?.cumulative_cost_inr ?? 0,
      lastMarginalPerCrore: last ? (last.riskRemovedPh / Math.max(1, last.costInr)) * 1e7 : 0,
      steps,
      loading: frontierLoading,
    };
  }, [frontier, plans, budgetInr, frontierLoading]);
}
