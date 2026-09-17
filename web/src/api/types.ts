/** Response shapes from the civic-twin API (spec 04). */
import type { Frame } from "../lib/frames";

export type Portfolio = "energy" | "water" | "comms" | "transport" | "services";
export type Provenance = "observed" | "inferred" | "synthetic";
export type PlanId = "baseline" | "asset_by_asset" | "optimised";
export type Service = "energy" | "water" | "comms" | "health" | "mobility";

export type Confidence = "high" | "medium" | "low";
export type OperatingState =
  | "operational"
  | "degraded"
  | "backup"
  | "critical"
  | "failed";
export type Evidence =
  | "observed"
  | "verified"
  | "estimated"
  | "assumed"
  | "simulated"
  | "external_model";
export type ObjectiveMode =
  | "balanced"
  | "protect_life"
  | "minimise_economic_loss"
  | "restore_fast";

/** A quantity reported with the precision it actually has (patch section 6). */
export interface Estimate {
  low: number;
  central: number;
  high: number;
  confidence: Confidence;
  unit: string;
  drivers: string[];
  interval: number;
  n_samples: number;
}

export interface Assumption {
  subject: string;
  statement: string;
  evidence: Evidence;
  source: string;
  uncertainty: string | null;
}

export interface Ledger {
  claim: string;
  standing: Evidence;
  counts: Record<string, number>;
  assumptions: Assumption[];
}

export interface ProvenanceSummary {
  counts: Record<string, number>;
  by_portfolio: Record<string, Record<string, number>>;
  total: number;
  sentence: string;
}

export interface RedundancyGroup {
  id: string;
  service: Service;
  members: string[];
  zones: string[];
  population: number;
  nominal_paths: number;
  independent_paths: number;
  score: number;
  weak: boolean;
  shared_dependencies: string[];
  shared_by_kind: Record<string, string>;
  common_roots: string[];
  explanation: string;
}

export interface RedundancyResponse extends Versioned {
  system_score: number;
  weak_groups: number;
  groups: RedundancyGroup[];
}

export interface Objective {
  mode: ObjectiveMode;
  label: string;
  description: string;
  weights: Record<string, number>;
  vulnerable_multiplier: number;
  recovery_weight: number;
}

export interface ObjectivesResponse extends Versioned {
  objectives: Objective[];
  default: ObjectiveMode;
}

export interface AssumptionsResponse extends Versioned {
  ledger: Ledger;
  provenance: ProvenanceSummary;
}

export interface SystemicRisk {
  failure_probability: number;
  failure_impact: number;
  dependency_concentration: number;
  recovery_difficulty: number;
  score: number;
}

export interface SeverityBand {
  probability: number;
  low: number;
  high: number;
  unit: string;
}

export interface HazardForecast {
  id: string;
  hazard_type: string;
  probability: number;
  start_time_h: number;
  duration_h: number;
  severity_distribution: SeverityBand[];
  uncertainty: string[];
  source: string;
  provenance: Provenance;
  notes: string | null;
  expected_severity: number;
  severity_range: [number, number];
}

export interface BandImpact {
  probability: number;
  severity_low_mm: number;
  severity_high_mm: number;
  scenarios: number;
  person_hours: Estimate;
  people_affected: Estimate;
}

export interface ForecastImpact {
  forecast_id: string;
  hazard_type: string;
  source: string;
  scenarios: number;
  person_hours: Estimate;
  people_affected: Estimate;
  people_affected_range: [number, number];
  tail_person_hours: Estimate;
  confidence: Confidence;
  primary_uncertainty: string[];
  bands: BandImpact[];
  worst_zone: string | null;
  most_likely_first_failure: string[];
}

export interface HazardImpactResponse extends Versioned {
  forecast: HazardForecast;
  impact: ForecastImpact;
  assumptions: Ledger;
}

export interface Versioned {
  data_version: string;
  model_version: string;
}

export interface Health extends Versioned {
  status: "ok" | "degraded";
  results_loaded: string[];
  results_missing: string[];
  copilot_available: boolean;
  role: string;
}

export interface AssetProperties {
  id: string;
  kind: string;
  portfolio: Portfolio;
  name: string;
  provenance: Provenance;
  capacity: number;
  backup_hours: number;
  fuel_hours: number;
  hand_m: number;
  x_m: number;
  y_m: number;
  scada_controlled: boolean;
  served_population: number;
  layer_altitude_m: number;
  fragility_median_m?: number;
}

export interface GeoFeature<P> {
  type: "Feature";
  id: string;
  geometry:
    | { type: "Point"; coordinates: [number, number] }
    | { type: "LineString"; coordinates: [number, number][] };
  properties: P;
}

export interface FeatureCollection<P> {
  type: "FeatureCollection";
  features: GeoFeature<P>[];
}

export interface ZoneProperties {
  id: string;
  population: number;
  vulnerable_fraction: number;
  hand_m: number;
  substation: string;
  feeder: string;
  tank: string;
  towers: string[];
  water_storage_hours: number;
  provenance: Provenance;
}

export interface RoadProperties {
  id: string;
  lanes: number;
  free_flow_kph: number;
  hand_m: number;
  host_asset: string | null;
  provenance: Provenance;
  layer_altitude_m: number;
}

export interface DependencyLink {
  source: string;
  target: string;
  kind: string;
  provenance: Provenance;
  source_lonlat: [number, number];
  target_lonlat: [number, number];
  source_altitude_m: number;
  target_altitude_m: number;
}

export interface TownshipResponse extends Versioned {
  name: string;
  bbox: [number, number, number, number];
  extent_m: number;
  layers: Record<Portfolio, FeatureCollection<AssetProperties>>;
  zones: FeatureCollection<ZoneProperties>;
  roads: FeatureCollection<RoadProperties>;
  links: DependencyLink[];
  provenance_summary: Record<Provenance, number>;
}

export interface Criticality {
  asset_id: string;
  portfolio: Portfolio;
  standalone_loss_ph: number;
  annual_failure_prob: number;
  tail_criticality_ph: number;
  eal_criticality_ph: number;
  systemic_ratio: number;
  systemic: boolean;
  expected_direct_ph: number;
  structural_importance: number;
  functional_importance: number;
  systemic_risk: SystemicRisk | null;
  served_population: number;
  recovery_criticality_h: number;
  explanation: string[];
}

export interface Spof {
  kind: "shared_dependency" | "colocation" | "common_cause" | "recovery";
  shared_asset: string | null;
  redundant_group: string[];
  affected_zones: string[];
  affected_population: number;
  service: Service;
  design_storm_failure_prob: number;
  explanation: string;
  counterfactual_ph: number;
  counterfactual_storm_ph: number;
  counterfactual_people: number;
  rank_score: number;
}

export interface AssetDetail extends Versioned {
  asset: AssetProperties;
  upstream?: string[];
  downstream?: string[];
  criticality?: Criticality | null;
  spofs?: Spof[];
  explanations?: string[];
  affected_zones: { id: string; population: number; services: string[] }[];
}

export interface TraceResponse extends Versioned {
  nodes: {
    id: string;
    kind: string;
    portfolio: Portfolio;
    name: string;
    provenance: Provenance;
    functionality: number;
    is_root: boolean;
  }[];
  edges: { source: string; target: string; kind: string }[];
  affected_population: number;
}

export interface SimResult {
  scenario_id: string;
  overlay_hash: string;
  weighted_loss_ph: number;
  loss_by_service_ph: Record<Service, number>;
  loss_by_zone_ph: Record<string, number>;
  vulnerable_loss_ph: number;
  peak_functionality_loss: Record<Portfolio, number>;
  recovery_90_h: Record<Portfolio, number>;
  damaged_assets: Record<string, string>;
  amplification_ratio: number;
  timeline?: Frame[];
}

export interface PeopleSummary {
  peak_no_power: number;
  peak_no_water: number;
  peak_no_comms: number;
  peak_no_health: number;
  person_hours_lost: number;
}

export interface ScenarioResponse extends Versioned {
  scenario_id: string;
  scenario: {
    id: string;
    rain_mm: number;
    onset_hour: number;
    return_period_y: number | null;
    label: string | null;
  };
  result: SimResult;
  people_summary: PeopleSummary;
  cached: boolean;
  plan_id?: string;
  interventions?: string[];
}

export interface PrecomputedScenario extends Versioned {
  scenario_id: string;
  rain_mm: number;
  return_period_y: number | null;
  plans: PlanId[];
}

export interface RiskResponse extends Versioned {
  alpha: number;
  n_scenarios: number;
  eal_ph: number;
  var95_ph: number;
  cvar95_ph: number;
  eal_ci95: [number, number];
  cvar95_ci95: [number, number];
  vulnerable_eal_ph: number;
  loss_samples_ph: number[];
  eal: Estimate;
  cvar95: Estimate;
  people_affected: Estimate;
  people_affected_range: [number, number];
  assumptions: Ledger;
  provenance: ProvenanceSummary;
  service_contributions_ph: Record<Service, number>;
  zone_contributions_ph: Record<string, number>;
  amplification: { mean: number; p05: number; p95: number };
  worst_zone: string;
}

export interface PlanStep {
  intervention_id: string;
  label: string;
  cumulative_cost_inr: number;
  cvar_after: number;
  marginal_cvar_reduction_per_lakh: number;
  /** Present on steps served by /frontier. */
  kind?: string;
  cost_inr?: number | null;
  target_asset?: string | null;
  why?: string;
}

export interface Plan {
  budget_inr: number;
  interventions: string[];
  cost_inr: number;
  cvar_before: number;
  cvar_after: number;
  eal_before: number;
  eal_after: number;
  cvar_reduction_pct: number;
  worst_zone_cvar_after: number;
  steps: PlanStep[];
  selection_frequency?: Record<string, number>;
  objective?: Objective;
  assumptions?: Ledger;
}

export interface PlanItem {
  id: string;
  label: string;
  kind: string;
  cost_inr: number;
  target_asset: string;
  selection_frequency: number | null;
  cvar_reduction_ph: number;
  why: string;
}

export interface PlansResponse extends Versioned {
  budget_inr: number;
  plan_budget_inr: number;
  plan: Plan;
  interventions: PlanItem[];
  frontier_position: { index: number; of: number };
}

export interface FrontierResponse extends Versioned {
  max_budget_inr: number;
  evaluated_on: string;
  steps: PlanStep[];
}

export interface BaselinesResponse extends Versioned {
  budget_inr: number;
  evaluated_on: string;
  baseline_cvar_ph: number;
  results: Record<
    string,
    { eal_ph: number; cvar_ph: number; n_interventions?: number; ci95?: [number, number] }
  >;
  optimised_vs_asset_by_asset_pct: number;
}

export interface CompareResponse extends Versioned {
  a: CompareSide;
  b: CompareSide;
  delta: {
    person_hours_lost: number;
    peak_no_power: number;
    peak_no_water: number;
    peak_no_comms: number;
    peak_no_health: number;
    recovery_90_h: Record<string, number | null>;
  };
  assets_changed: string[];
}

export interface CompareSide {
  scenario_id: string;
  plan_id: string;
  person_hours_lost: number;
  vulnerable_loss_ph: number;
  recovery_90_h: Record<string, number>;
  damaged_assets: string[];
  peak_no_power: number;
  peak_no_water: number;
  peak_no_comms: number;
  peak_no_health: number;
}

export type DecisionStatus = "approved" | "approved_with_changes" | "deferred" | "rejected";

export interface PlanSnapshot {
  budget_inr: number;
  cost_inr: number;
  measures: number;
  cvar_reduction_pct: number;
  objective: string;
}

export interface DecisionRecord extends Versioned {
  plan_id: string;
  rationale: string;
  scenarios_considered: string[];
  author: string;
  recorded_at: string;
  status?: DecisionStatus;
  plan_summary?: PlanSnapshot;
}

export interface CopilotResponse {
  available: boolean;
  answer: string | null;
  tool_calls: { tool: string; args: Record<string, unknown>; summary: string }[];
  reason: string | null;
}

export interface RestorationResponse extends Versioned {
  scenario_id: string;
  policies: Record<
    string,
    {
      policy: string;
      order: [string, string][];
      area_under_loss_ph: number;
      recovery_90_h: number;
    }
  >;
}

export interface ScenarioRequest {
  rain_mm: number;
  field_seed?: number;
  onset_hour?: number;
  forced_failures?: string[];
  interventions?: string[];
  record?: boolean;
  /** Which named plan this run represents, so switching plans can re-run it. */
  plan?: PlanId;
}
