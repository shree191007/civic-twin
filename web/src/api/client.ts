/** Fetch wrapper with a fixture fallback, so the demo survives a dead API. */

const BASE = import.meta.env.VITE_API_BASE ?? "/api";
const FIXTURES = "/fixtures";

export class ApiError extends Error {
  constructor(readonly status: number, readonly body: unknown, message: string) {
    super(message);
  }
}

let usingFixtures = false;
const listeners = new Set<(v: boolean) => void>();

export function onFixtureModeChange(fn: (v: boolean) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export const isUsingFixtures = (): boolean => usingFixtures;

function setFixtureMode(value: boolean): void {
  if (usingFixtures === value) return;
  usingFixtures = value;
  listeners.forEach((fn) => fn(value));
}

/** Maps an API path to the fixture file that stands in for it offline. */
function fixturePath(path: string): string | null {
  const clean = path.split("?")[0];
  const direct: Record<string, string> = {
    "/risk": "risk.json",
    "/criticality": "criticality.json",
    "/spofs": "spofs.json",
    "/critical-sets": "critical_sets.json",
    "/frontier": "frontier.json",
    "/baselines": "baselines.json",
    "/voi": "voi.json",
    "/sensitivity": "sensitivity.json",
    "/ensemble": "ensemble.json",
    "/township": "township.json",
    "/scenarios/precomputed": "precomputed.json",
  };
  if (direct[clean]) return `${FIXTURES}/${direct[clean]}`;
  const hero = clean.match(/^\/scenarios\/(storm_\w+)$/);
  if (hero) {
    const plan = new URLSearchParams(path.split("?")[1] ?? "").get("plan") ?? "baseline";
    return `${FIXTURES}/hero/${hero[1]}__${plan}.json`;
  }
  return null;
}

//: An API that is up but has no results for this path is as useless to the
//: demo as one that is down, so both fall back to the fixtures.
const FALLBACK_STATUSES = new Set([404, 503]);

export async function get<T>(path: string): Promise<T> {
  try {
    const response = await fetch(`${BASE}${path}`);
    if (!response.ok) {
      throw new ApiError(response.status, await safeJson(response), `GET ${path}`);
    }
    setFixtureMode(false);
    return (await response.json()) as T;
  } catch (error) {
    const recoverable =
      !(error instanceof ApiError) || FALLBACK_STATUSES.has(error.status);
    const fallback = recoverable ? fixturePath(path) : null;
    if (!fallback) throw error;
    const response = await fetch(fallback);
    if (!response.ok) throw error;
    setFixtureMode(true);
    return (await response.json()) as T;
  }
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await safeJson(response), `POST ${path}`);
  }
  setFixtureMode(false);
  return (await response.json()) as T;
}

async function safeJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}
