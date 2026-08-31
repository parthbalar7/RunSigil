import { useState } from "react";
import type { Approval } from "../types";

export function ApprovalPanel({
  approval,
  busy,
  onDecision,
}: {
  approval: Approval;
  busy: boolean;
  onDecision: (decision: "approve" | "deny", reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("Reviewed against production policy");
  const pending = approval.status === "pending";

  return (
    <aside className="approval-panel" aria-labelledby="approval-title">
      <div className="approval-header">
        <div>
          <p className="eyebrow">Decision required</p>
          <h2 id="approval-title">Production side effect</h2>
        </div>
        <span className={`pill status-${approval.status}`}>{approval.status}</span>
      </div>
      <p className="approval-reason">{approval.reason}</p>
      <dl className="fact-list">
        {Object.entries(approval.request_preview).map(([key, value]) => (
          <div key={key}>
            <dt>{key.replaceAll("_", " ")}</dt>
            <dd>{String(value)}</dd>
          </div>
        ))}
      </dl>
      <div className="digest-box">
        <span>Exact content digest</span>
        <code>{approval.content_digest}</code>
        <p>Approval cannot authorize changed arguments. Any change creates a new request.</p>
      </div>
      {pending && (
        <>
          <label className="field">
            <span>Decision justification</span>
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={3} />
          </label>
          <div className="approval-actions">
            <button
              type="button"
              className="button danger-secondary"
              disabled={busy || reason.trim().length < 2}
              onClick={() => void onDecision("deny", reason)}
            >
              Deny action
            </button>
            <button
              type="button"
              className="button primary"
              disabled={busy || reason.trim().length < 2}
              onClick={() => void onDecision("approve", reason)}
            >
              {busy ? "Saving decision…" : "Approve exact action"}
            </button>
          </div>
        </>
      )}
    </aside>
  );
}

