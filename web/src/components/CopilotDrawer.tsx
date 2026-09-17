/** The copilot drawer. Prose only — every stat block on screen comes from the API. */
import { useState } from "react";
import { post } from "../api/client";
import { useStore } from "../store";
import type { CopilotResponse } from "../api/types";

const ASSET_ID = /\b([A-Z]{1,5}[0-9]{1,3}|FIBRE_[0-9])\b/g;

/** Renders asset ids in the answer as clickable chips. */
function Answer({ text, onPick }: { text: string; onPick: (id: string) => void }) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(ASSET_ID)) {
    const start = match.index ?? 0;
    if (start > last) parts.push(text.slice(last, start));
    parts.push(
      <button
        key={`${start}-${match[0]}`}
        className="mono"
        style={{ fontSize: 13, padding: "0 4px" }}
        onClick={() => onPick(match[0])}
      >
        {match[0]}
      </button>,
    );
    last = start + match[0].length;
  }
  parts.push(text.slice(last));
  return <p style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{parts}</p>;
}

export function CopilotDrawer() {
  const open = useStore((s) => s.copilotOpen);
  const setOpen = useStore((s) => s.setCopilotOpen);
  const selectAsset = useStore((s) => s.selectAsset);
  const scene = useStore((s) => ({
    selected_asset: s.selectedAsset,
    scenario_id: s.scenarioId.startsWith("adhoc:") ? null : s.scenarioId,
    t: s.t,
    active_layers: [...s.activeLayers],
    plan: s.plan,
  }));

  const [question, setQuestion] = useState("");
  const [response, setResponse] = useState<CopilotResponse | null>(null);
  const [pending, setPending] = useState(false);

  const ask = async () => {
    if (!question.trim()) return;
    setPending(true);
    try {
      setResponse(await post<CopilotResponse>("/copilot", { question, scene }));
    } catch {
      setResponse({ available: false, answer: null, tool_calls: [], reason: "Copilot unreachable" });
    } finally {
      setPending(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className="panel copilot-drawer scroll"
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        bottom: 0,
        width: 340,
        padding: 12,
        zIndex: 20,
        borderRadius: 0,
      }}
    >
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>Copilot</h2>
        <button onClick={() => setOpen(false)} aria-label="Close copilot">×</button>
      </div>
      <p className="dim" style={{ fontSize: 12, lineHeight: 1.5 }}>
        Answers come from tool calls against this model. It cannot invent numbers,
        and it cannot commit spending.
      </p>
      <textarea
        rows={3}
        value={question}
        placeholder="What happens if this fails?"
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void ask();
        }}
      />
      <div className="row" style={{ marginTop: 6 }}>
        <button onClick={() => void ask()} disabled={pending}>
          {pending ? "Thinking…" : "Ask"}
        </button>
        <span className="spacer" />
        <span className="dim mono" style={{ fontSize: 11 }}>⌘↵ to send</span>
      </div>

      {response && !response.available && (
        <div className="dim" style={{ fontSize: 13, marginTop: 12 }}>
          {response.reason ?? "Copilot not configured."}
        </div>
      )}

      {response?.available && (
        <>
          {response.answer && <Answer text={response.answer} onPick={selectAsset} />}
          <details open>
            <summary className="dim mono" style={{ fontSize: 12, cursor: "pointer" }}>
              tool trace ({response.tool_calls.length})
            </summary>
            <ol style={{ paddingLeft: 16, margin: "6px 0 0" }}>
              {response.tool_calls.map((call, i) => (
                <li key={i} style={{ fontSize: 12, marginBottom: 5 }}>
                  <span className="mono" style={{ color: "var(--water-text)" }}>{call.tool}</span>
                  <div className="dim mono" style={{ fontSize: 11 }}>
                    {JSON.stringify(call.args)}
                  </div>
                  <div className="dim">{call.summary}</div>
                </li>
              ))}
            </ol>
          </details>
        </>
      )}
    </div>
  );
}
