/** The decision log and the printable brief. */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useDecisions } from "../../api/queries";
import { post } from "../../api/client";
import { Brief } from "../../components/Brief";
import { useStore } from "../../store";
import type { DecisionRecord } from "../../api/types";

export function Log() {
  const { data: decisions } = useDecisions();
  const budget = useStore((s) => s.budget);
  const scenarioId = useStore((s) => s.scenarioId);
  const queryClient = useQueryClient();
  const [rationale, setRationale] = useState("");
  const [author, setAuthor] = useState("planner");
  const [saving, setSaving] = useState(false);

  const record = async () => {
    if (!rationale.trim()) return;
    setSaving(true);
    try {
      await post<DecisionRecord>("/decisions", {
        plan_id: `budget_${Math.round(budget)}`,
        rationale,
        scenarios_considered: [scenarioId],
        author,
      });
      setRationale("");
      await queryClient.invalidateQueries({ queryKey: ["decisions"] });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="scroll" style={{ padding: 8, height: "100%", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, alignItems: "start" }}>
      <div className="col">
        <div className="panel no-print" style={{ padding: 10 }}>
          <h3>Record this plan</h3>
          <div className="col" style={{ marginTop: 6 }}>
            <label className="dim" style={{ fontSize: 10 }}>
              rationale
              <textarea rows={3} value={rationale} onChange={(e) => setRationale(e.target.value)} />
            </label>
            <label className="dim" style={{ fontSize: 10 }}>
              author
              <input type="text" value={author} onChange={(e) => setAuthor(e.target.value)} />
            </label>
            <div className="row">
              <button onClick={() => void record()} disabled={saving || !rationale.trim()}>
                {saving ? "Recording…" : "Record decision"}
              </button>
              <span className="spacer" />
              <button onClick={() => window.print()}>Generate brief (print)</button>
            </div>
          </div>
        </div>

        <div className="panel no-print" style={{ padding: 10 }}>
          <h3>Decisions on record</h3>
          <table style={{ marginTop: 6 }}>
            <thead>
              <tr>
                <th>when</th>
                <th>plan</th>
                <th>rationale</th>
                <th>author</th>
              </tr>
            </thead>
            <tbody>
              {(decisions?.items ?? []).map((d, i) => (
                <tr key={i}>
                  <td className="mono" style={{ fontSize: 10 }}>{d.recorded_at.slice(0, 16).replace("T", " ")}</td>
                  <td className="mono" style={{ fontSize: 10 }}>{d.plan_id}</td>
                  <td>{d.rationale}</td>
                  <td className="dim">{d.author}</td>
                </tr>
              ))}
              {(decisions?.items ?? []).length === 0 && (
                <tr>
                  <td colSpan={4} className="dim">No decisions recorded yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <Brief />
    </div>
  );
}
