/** Frame helpers: reading a timeline frame and composing the HUD readout. */
import { people } from "./format";

export interface Frame {
  t: number;
  flood: Record<string, number>;
  func: Record<string, number>;
  damage: Record<string, string>;
  /** Operating state, for assets that are not simply operational. */
  state: Record<string, string>;
  reason: Record<string, string>;
  closed_roads: string[];
  crews: { id: string; node: number; task: string; eta_h: number }[];
  zones: Record<string, Record<string, number>>;
  totals: {
    people_no_power: number;
    people_no_water: number;
    people_no_comms: number;
    people_no_health: number;
  };
}

export const EMPTY_FRAME: Frame = {
  t: 0,
  flood: {},
  func: {},
  damage: {},
  state: {},
  reason: {},
  closed_roads: [],
  crews: [],
  zones: {},
  totals: {
    people_no_power: 0,
    people_no_water: 0,
    people_no_comms: 0,
    people_no_health: 0,
  },
};

/** The frame at or immediately before hour `t`. Status never interpolates. */
export function frameAt(timeline: Frame[] | undefined, t: number): Frame {
  if (!timeline || timeline.length === 0) return EMPTY_FRAME;
  let chosen = timeline[0];
  for (const frame of timeline) {
    if (frame.t <= t + 1e-9) chosen = frame;
    else break;
  }
  return chosen;
}

export function functionalityOf(frame: Frame, assetId: string): number {
  const f = frame.func[assetId];
  return f === undefined ? 1 : f;
}

export function floodAt(frame: Frame, assetId: string): number {
  return frame.flood[assetId] ?? 0;
}

const KIND_WORDS: Record<string, string> = {
  pump: "pump",
  substation: "substation",
  tower: "tower",
  exchange: "exchange",
  bridge: "bridge",
  treatment: "treatment works",
};

/**
 * The one-line scenario readout under the HUD. Composed here from the frame's
 * own totals and reasons — never from the copilot.
 */
export function readout(
  frame: Frame,
  kindOf: (assetId: string) => string | undefined,
): string {
  const parts: string[] = [`H+${Math.round(frame.t)}`];

  const outByKind = new Map<string, number>();
  const onBackup = new Map<string, number>();
  for (const [assetId, state] of Object.entries(frame.state)) {
    const word = KIND_WORDS[kindOf(assetId) ?? ""];
    if (!word) continue;
    if (state === "failed") outByKind.set(word, (outByKind.get(word) ?? 0) + 1);
    else if (state === "backup" || state === "critical") {
      onBackup.set(word, (onBackup.get(word) ?? 0) + 1);
    }
  }
  // Fall back to functionality for a timeline recorded before operating states.
  if (outByKind.size === 0) {
    for (const [assetId, f] of Object.entries(frame.func)) {
      if (f > 0) continue;
      const word = KIND_WORDS[kindOf(assetId) ?? ""];
      if (word) outByKind.set(word, (outByKind.get(word) ?? 0) + 1);
    }
  }
  for (const [word, n] of [...outByKind.entries()].sort((a, b) => b[1] - a[1]).slice(0, 2)) {
    parts.push(`${n} ${word}${n === 1 ? "" : "s"} down`);
  }
  const backupTotal = [...onBackup.values()].reduce((a, b) => a + b, 0);
  if (backupTotal > 0) parts.push(`${backupTotal} on backup`);

  if (frame.closed_roads.length > 0) {
    parts.push(`${frame.closed_roads.length} roads impassable`);
  }
  if (frame.totals.people_no_health > 0) {
    parts.push(`${people(frame.totals.people_no_health)} without hospital access`);
  } else if (frame.totals.people_no_power > 0) {
    parts.push(`${people(frame.totals.people_no_power)} without power`);
  }
  if (parts.length === 1) parts.push("all services nominal");
  return parts.join(" · ");
}
