/** Decisions & brief: record what the council decided, and print the brief. */
import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useDecisions, useObjectives, usePrecomputed } from "../../api/queries";
import { post } from "../../api/client";
import { Brief } from "../../components/Brief";
import { useStore } from "../../store";
import { usePackage } from "../../lib/usePackage";
import { inr, returnPeriod } from "../../lib/format";
import type { DecisionRecord, DecisionStatus, PlanSnapshot } from "../../api/types";

const STATUSES: { value: DecisionStatus; label: string }[] = [
  { value: "approved", label: "Approve" },
  { value: "approved_with_changes", label: "Approve with changes" },
  { value: "deferred", label: "Defer" },
  { value: "rejected", label: "Reject" },
];

const STATUS_LABEL: Record<DecisionStatus, string> = {
  approved: "Approved",
  approved_with_changes: "Approved with changes",
  deferred: "Deferred",
  rejected: "Rejected",
};

const RATIONALE_MIN = 10;

export function Log() {
  const { data: decisions } = useDecisions();
  const { data: storms } = usePrecomputed();
  const { data: objectives } = useObjectives();
  const budget = useStore((s) => s.budget);
  const objective = useStore((s) => s.objective);
  const pkg = usePackage(budget);
  const queryClient = useQueryClient();

  const [status, setStatus] = useState<DecisionStatus>("approved");
  const [considered, setConsidered] = useState<string[]>(["storm_50y", "storm_100y"]);
  const [rationale, setRationale] = useState("");
  const [author, setAuthor] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const objectiveLabel =
    objectives?.objectives.find((o) => o.mode === objective)?.label ?? "Balanced strategy";

  const snapshot: PlanSnapshot = {
    budget_inr: budget,
    cost_inr: pkg.costInr,
    measures: pkg.items.length,
    cvar_reduction_pct: Math.round(pkg.reductionPct * 10) / 10,
    objective: objectiveLabel,
  };

  const history = useMemo(
    () => [...(decisions?.items ?? [])].sort((a, b) => b.recorded_at.localeCompare(a.recorded_at)),
    [decisions],
  );

  const canSave = rationale.trim().length >= RATIONALE_MIN && author.trim().length > 0 && !saving;

  const record = async () => {
    if (!canSave) return;
    setSaving(true);
    setMessage(null);
    try {
      await post<DecisionRecord>("/decisions", {
        plan_id: `budget_${Math.round(budget)}`,
        rationale: rationale.trim(),
        scenarios_considered: considered,
        author: author.trim(),
        status,
        plan_summary: snapshot,
      });
      setRationale("");
      setMessage({ kind: "ok", text: `Decision recorded: ${STATUS_LABEL[status].toLowerCase()}.` });
      await queryClient.invalidateQueries({ queryKey: ["decisions"] });
    } catch {
      setMessage({ kind: "error", text: "The decision could not be saved. Is the API running?" });
    } finally {
      setSaving(false);
    }
  };

  const toggleStorm = (id: string) =>
    setConsidered((current) =>
      current.includes(id) ? current.filter((s) => s !== id) : [...current, id],
    );

  return (
    <div className="page">
      <div className="page-inner" style={{ maxWidth: 1400 }}>
        <header className="page-header no-print">
          <div>
            <h1 className="page-title">Decisions & brief</h1>
            <p className="page-lede">
              Record what was decided about the investment plan, and print a one-page brief for the council.
            </p>
          </div>
          <button className="primary" onClick={() => window.print()}>
            Print or save the brief as PDF
          </button>
        </header>

        <div className="log-grid">
          <div className="col log-side no-print" style={{ gap: 20 }}>
            {/* --------------------------------------------------- record */}
            <section className="card" aria-labelledby="record-title">
              <h2 className="section-title" id="record-title">Record a decision</h2>
              <p className="section-sub">It is saved with the plan as it stands right now.</p>

              <div className="field">
                <span className="field-label">Plan being decided</span>
                <div className="snapshot">
                  <div>
                    Budget
                    <b className="mono">{inr(snapshot.budget_inr)}</b>
                  </div>
                  <div>
                    Allocated
                    <b className="mono">{inr(snapshot.cost_inr)}</b>
                  </div>
                  <div>
                    Measures
                    <b>{snapshot.measures}</b>
                  </div>
                  <div>
                    Worst-year damage
                    <b style={{ color: "var(--f-full-text)" }}>−{snapshot.cvar_reduction_pct}%</b>
                  </div>
                  <div style={{ gridColumn: "1 / -1" }}>
                    Priority
                    <b style={{ fontSize: 14 }}>{snapshot.objective}</b>
                  </div>
                </div>
                <span className="field-hint">Change these on the Investment plan tab.</span>
              </div>

              <fieldset className="field" style={{ border: 0, padding: 0, margin: "16px 0 0" }}>
                <legend className="field-label" style={{ marginBottom: 6 }}>Decision</legend>
                <div className="status-options">
                  {STATUSES.map((s) => (
                    <label
                      key={s.value}
                      className={status === s.value ? "status-option status-option-on" : "status-option"}
                    >
                      <input
                        type="radio"
                        name="status"
                        checked={status === s.value}
                        onChange={() => setStatus(s.value)}
                      />
                      {s.label}
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset className="field" style={{ border: 0, padding: 0, margin: "16px 0 0" }}>
                <legend className="field-label" style={{ marginBottom: 6 }}>Storms considered</legend>
                <div className="checks">
                  {[...(storms ?? [])]
                    .sort((a, b) => (a.return_period_y ?? 0) - (b.return_period_y ?? 0))
                    .map((storm) => (
                    <label key={storm.scenario_id} className="check">
                      <input
                        type="checkbox"
                        checked={considered.includes(storm.scenario_id)}
                        onChange={() => toggleStorm(storm.scenario_id)}
                      />
                      {returnPeriod(storm.return_period_y)}
                    </label>
                  ))}
                </div>
              </fieldset>

              <label className="field">
                <span className="field-label">Reason</span>
                <textarea
                  rows={4}
                  value={rationale}
                  placeholder="e.g. Approved for the 2027 monsoon budget; bridge B1 survey to report before phase two."
                  onChange={(e) => setRationale(e.target.value)}
                />
                <span className="field-hint">
                  {rationale.trim().length < RATIONALE_MIN
                    ? `At least ${RATIONALE_MIN} characters, so the record makes sense later.`
                    : "Looks good."}
                </span>
              </label>

              <label className="field">
                <span className="field-label">Recorded by</span>
                <input
                  type="text"
                  value={author}
                  placeholder="Name and role"
                  onChange={(e) => setAuthor(e.target.value)}
                />
              </label>

              <div className="row" style={{ marginTop: 18, gap: 12 }}>
                <button className="primary" onClick={() => void record()} disabled={!canSave}>
                  {saving ? "Saving…" : "Save decision"}
                </button>
                {message && (
                  <span
                    role="status"
                    style={{
                      fontSize: 13.5,
                      color: message.kind === "ok" ? "var(--f-full-text)" : "var(--f-down-text)",
                    }}
                  >
                    {message.text}
                  </span>
                )}
              </div>
            </section>

            {/* -------------------------------------------------- history */}
            <section className="card" aria-labelledby="history-title">
              <div className="section-head" style={{ marginBottom: 8 }}>
                <h2 className="section-title" id="history-title">Decision history</h2>
                <span className="dim" style={{ fontSize: 13 }}>{history.length} recorded</span>
              </div>
              {history.length === 0 ? (
                <div className="empty">No decisions recorded yet.</div>
              ) : (
                <div className="history">
                  {history.map((d, i) => (
                    <article key={`${d.recorded_at}-${i}`} className="history-item">
                      <div className="history-meta">
                        <span className={`status-chip status-${d.status ?? "none"}`}>
                          {d.status ? STATUS_LABEL[d.status] : "Recorded"}
                        </span>
                        <span>{formatWhen(d.recorded_at)}</span>
                        <span aria-hidden="true">·</span>
                        <span>{d.author}</span>
                      </div>
                      <p className="history-text">{d.rationale}</p>
                      <div className="history-plan">
                        {d.plan_summary
                          ? `${inr(d.plan_summary.cost_inr)} · ${d.plan_summary.measures} measures · −${d.plan_summary.cvar_reduction_pct}% worst-year damage · ${d.plan_summary.objective}`
                          : `Plan ${d.plan_id.replace("budget_", "budget ₹")}`}
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>

          {/* ------------------------------------------------------ preview */}
          <section aria-label="Brief preview">
            <div className="row no-print" style={{ justifyContent: "space-between", marginBottom: 10 }}>
              <h2 className="section-title">Council brief preview</h2>
              <span className="dim" style={{ fontSize: 13 }}>A4, one printed page per section block</span>
            </div>
            <div className="paper-wrap">
              <Brief />
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function formatWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
