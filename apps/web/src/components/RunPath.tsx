import type { RunDetail, TraceEvent } from "../types";

const nodes = [
  { id: "policy-check", label: "Policy check", caption: "Fail-closed decision" },
  { id: "human-approval", label: "Human approval", caption: "Exact content" },
  { id: "action-dispatch", label: "Action dispatch", caption: "Durable claim" },
  { id: "action-reconciliation", label: "Reconciliation", caption: "Only if ambiguous" },
  { id: "evidence-seal", label: "Evidence", caption: "Ed25519 bundle" },
];

function nodeStatus(run: RunDetail, nodeId: string): string {
  if (run.active_node === nodeId) return "active";
  if (nodeId === "policy-check") return "complete";
  if (nodeId === "human-approval") {
    return run.approval?.status === "pending" ? "active" : run.approval ? "complete" : "skipped";
  }
  if (nodeId === "action-dispatch") {
    return run.action?.state === "committed" ? "complete" : run.action?.state === "failed" ? "failed" : "queued";
  }
  if (nodeId === "action-reconciliation") {
    return run.action?.reconcile_attempts ? (run.action.state === "committed" ? "complete" : "active") : "skipped";
  }
  if (nodeId === "evidence-seal") return run.evidence_status === "pending" ? "queued" : "complete";
  return "queued";
}

export function RunPath({
  run,
  selectedNode,
  onSelect,
}: {
  run: RunDetail;
  selectedNode: string | null;
  onSelect: (nodeId: string | null) => void;
}) {
  if (run.workflow) {
    const attemptsByPosition = run.workflow.path.map((nodeId, index) => {
      const matching = run.workflow?.attempts.filter((attempt) => attempt.node_id === nodeId) ?? [];
      const previousOccurrences = run.workflow?.path.slice(0, index).filter((id) => id === nodeId).length ?? 0;
      return matching[previousOccurrences];
    });
    const currentNodes = run.workflow.current_nodes.filter((nodeId) => !run.workflow?.path.includes(nodeId));
    const workflowNodes = [
      ...run.workflow.path.map((nodeId, index) => ({
        key: `${nodeId}-${index}`,
        id: nodeId,
        label: nodeId.replaceAll("_", " "),
        caption: attemptsByPosition[index]?.node_type ?? "workflow node",
        status: attemptsByPosition[index]?.status ?? "complete",
      })),
      ...currentNodes.map((nodeId) => {
        const wait = run.workflow?.waits
          .filter((candidate) => candidate.node_id === nodeId)
          .sort((left, right) => right.sequence - left.sequence)[0];
        const subworkflow = run.workflow?.subworkflows
          ?.filter((candidate) => candidate.node_id === nodeId)
          .sort((left, right) => left.id.localeCompare(right.id))[0];
        const toolCall = run.workflow?.tool_calls
          ?.filter((candidate) => candidate.node_id === nodeId)
          .sort((left, right) => left.id.localeCompare(right.id))[0];
        const toolSimulation = run.workflow?.tool_simulations
          ?.filter((candidate) => candidate.node_id === nodeId)
          .sort((left, right) => left.id.localeCompare(right.id))[0];
        const modelCall = run.workflow?.model_calls
          ?.filter((candidate) => candidate.node_id === nodeId)
          .sort((left, right) => left.id.localeCompare(right.id))[0];
        return {
          key: `${nodeId}-current`,
          id: nodeId,
          label: nodeId.replaceAll("_", " "),
          caption: wait
            ? `${wait.wait_type.replaceAll("_", " ")} wait · ${wait.status}`
            : subworkflow
              ? `referenced workflow · ${subworkflow.status}`
              : toolCall
                ? `governed tool · ${toolCall.status.replaceAll("_", " ")}`
                : toolSimulation
                  ? "deterministic tool simulation · completed"
                  : modelCall
                    ? `governed model · ${modelCall.status.replaceAll("_", " ")}`
                    : "next durable node",
          status:
            wait?.status === "pending" ||
            subworkflow?.status === "pending" ||
            toolCall?.status === "pending_approval" ||
            toolCall?.status === "queued" ||
            toolCall?.status === "executing" ||
            toolCall?.status === "reconciliation_required" ||
            toolCall?.status === "reconciling" ||
            modelCall?.status === "queued" ||
            modelCall?.status === "executing" ||
            modelCall?.status === "reconciliation_required" ||
            modelCall?.status === "reconciling"
              ? "waiting"
              : toolCall?.status === "failed" ||
                  toolCall?.status === "dead_lettered" ||
                  toolCall?.status === "cancelled" ||
                  toolCall?.status === "timed_out" ||
                  modelCall?.status === "failed" ||
                  modelCall?.status === "timed_out"
                ? "failed"
              : "active",
        };
      }),
    ];
    return (
      <section className="panel path-panel" aria-labelledby="run-path-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">
              Durable Workflow Engine v2 · {run.workflow.execution_mode}
            </p>
            <h2 id="run-path-title">Executed trajectory</h2>
          </div>
          <button className="text-button" onClick={() => onSelect(null)} type="button">
            Show all events
          </button>
        </div>
        <div className="run-path" role="list" aria-label="Text representation of the executed workflow trajectory">
          {workflowNodes.map((node, index) => {
            const selected = selectedNode === node.id;
            return (
              <div className="path-step-wrap" key={node.key}>
                <button
                  type="button"
                  className={`path-step status-${node.status}${selected ? " selected" : ""}`}
                  aria-pressed={selected}
                  aria-label={`${node.label}: ${node.status}. ${node.caption}`}
                  onClick={() => onSelect(selected ? null : node.id)}
                >
                  <span className="step-index" aria-hidden="true">{index + 1}</span>
                  <span><strong>{node.label}</strong><small>{node.caption}</small></span>
                  <span className="step-status">{node.status}</span>
                </button>
                {index < workflowNodes.length - 1 && <span className="path-line" aria-hidden="true" />}
              </div>
            );
          })}
        </div>
      </section>
    );
  }
  return (
    <section className="panel path-panel" aria-labelledby="run-path-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Executable path</p>
          <h2 id="run-path-title">Governed action</h2>
        </div>
        <button className="text-button" onClick={() => onSelect(null)} type="button">
          Show all events
        </button>
      </div>
      <div className="run-path" role="list" aria-label="Text representation of the governed action workflow">
        {nodes.map((node, index) => {
          const status = nodeStatus(run, node.id);
          const selected = selectedNode === node.id;
          return (
            <div className="path-step-wrap" key={node.id}>
              <button
                type="button"
                className={`path-step status-${status}${selected ? " selected" : ""}`}
                aria-pressed={selected}
                aria-label={`${node.label}: ${status}. ${node.caption}`}
                onClick={() => onSelect(selected ? null : node.id)}
              >
                <span className="step-index" aria-hidden="true">
                  {index + 1}
                </span>
                <span>
                  <strong>{node.label}</strong>
                  <small>{node.caption}</small>
                </span>
                <span className="step-status">{status}</span>
              </button>
              {index < nodes.length - 1 && <span className="path-line" aria-hidden="true" />}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function Timeline({
  events,
  selectedEvent,
  onSelect,
}: {
  events: TraceEvent[];
  selectedEvent: string | null;
  onSelect: (event: TraceEvent) => void;
}) {
  return (
    <section className="panel timeline-panel" aria-labelledby="timeline-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Correlated trace</p>
          <h2 id="timeline-title">Timeline</h2>
        </div>
        <span className="count">{events.length} events</span>
      </div>
      {events.length === 0 ? (
        <div className="empty-state">No events match the selected workflow node.</div>
      ) : (
        <ol className="timeline-list">
          {events.map((event) => (
            <li key={event.id}>
              <button
                type="button"
                className={`timeline-event${selectedEvent === event.id ? " selected" : ""}`}
                onClick={() => onSelect(event)}
                aria-pressed={selectedEvent === event.id}
              >
                <span className={`event-dot status-${event.status}`} aria-hidden="true" />
                <span className="event-main">
                  <strong>{event.event_type.replaceAll(".", " / ")}</strong>
                  <span>{event.node_id}</span>
                </span>
                <time>{new Date(event.created_at).toLocaleTimeString()}</time>
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
