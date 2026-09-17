/** Transport controls: scrub 0-72 h, play, and speed. */
import { useEffect, useRef } from "react";
import { HORIZON_H, useStore, type Speed } from "../store";

const SPEEDS: Speed[] = [0.25, 0.5, 1, 2, 4];
const TICK_H = 6;
const FRAME_MS = 250;

export function TimelineScrubber() {
  const t = useStore((s) => s.t);
  const setT = useStore((s) => s.setT);
  const stepT = useStore((s) => s.stepT);
  const playing = useStore((s) => s.playing);
  const togglePlaying = useStore((s) => s.togglePlaying);
  const speed = useStore((s) => s.speed);
  const setSpeed = useStore((s) => s.setSpeed);

  // Advance by speed * dt_h per 250 ms of wall clock.
  const last = useRef(0);
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    last.current = performance.now();
    const tick = (now: number) => {
      if (now - last.current >= FRAME_MS) {
        stepT(speed * ((now - last.current) / FRAME_MS));
        last.current = now;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, stepT]);

  const ticks = Array.from({ length: HORIZON_H / TICK_H + 1 }, (_, i) => i * TICK_H);

  return (
    <div className="row" style={{ gap: 10, width: "100%" }}>
      <button onClick={togglePlaying} aria-label={playing ? "Pause" : "Play"} style={{ width: 34 }}>
        {playing ? "❚❚" : "▶"}
      </button>
      <div style={{ flex: 1, position: "relative" }}>
        <input
          type="range"
          min={0}
          max={HORIZON_H}
          step={1}
          value={t}
          onChange={(e) => setT(Number(e.target.value))}
          aria-label="Hours since the storm began"
          style={{ width: "100%" }}
        />
        <div className="row mono" style={{ justifyContent: "space-between", fontSize: 11, color: "var(--text-dim)" }}>
          {ticks.map((h) => (
            <span key={h}>{h}</span>
          ))}
        </div>
      </div>
      <span className="mono" style={{ minWidth: 52, textAlign: "right" }}>H+{Math.round(t)}</span>
      <div className="row" style={{ gap: 3 }}>
        {SPEEDS.map((s) => (
          <button
            key={s}
            aria-pressed={speed === s}
            onClick={() => setSpeed(s)}
            className="mono"
            style={{ fontSize: 12, padding: "2px 5px" }}
          >
            {s}×
          </button>
        ))}
      </div>
    </div>
  );
}
