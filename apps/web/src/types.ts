export type Resource = { id: string; name: string; slug?: string };

export type WorkspaceContext = {
  organization: Resource;
  projects: Resource[];
  environments: Array<Resource & { environment_type: string; protected: boolean }>;
  systems: Array<Resource & { project_id: string; risk_tier: string }>;
  agents: Array<Resource & { system_id: string; framework: string }>;
};

export type Approval = {
  id: string;
  run_id: string;
  status: string;
  risk: string;
  reason: string;
  content_digest: string;
  request_preview: Record<string, unknown>;
  expires_at: string;
};

export type TraceEvent = {
  id: string;
  node_id: string;
  span_id: string;
  event_type: string;
  status: string;
  sequence: number;
  attributes: Record<string, unknown>;
  created_at: string;
};

export type WorkflowWait = {
  id: string;
  run_id: string;
  workflow_execution_id: string;
  node_id: string;
  sequence: number;
  wait_type: "timer" | "approval" | "request_information" | "event";
  status: "pending" | "resolved" | "expired" | "cancelled";
  resolution: string | null;
  content_digest: string;
  state_digest: string;
  request_metadata: Record<string, unknown>;
  event_key: string | null;
  due_at: string | null;
  expires_at: string;
  response_digest: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  created_at: string;
};

export type RunDetail = {
  id: string;
  run_kind: "governed_action" | "workflow";
  status: string;
  project_id: string;
  environment_id: string;
  agent_id: string;
  active_node: string | null;
  input_digest: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  action: null | {
    id: string;
    tool_name: string;
    state: string;
    content_digest: string;
    request_preview: Record<string, unknown>;
    receipt_preview: Record<string, unknown> | null;
    execute_attempts: number;
    reconcile_attempts: number;
    error_code: string | null;
  };
  approval: Approval | null;
  workflow: null | {
    id: string;
    workflow_version_id: string;
    deployment_id: string;
    execution_mode: "live" | "simulation";
    simulation_profile_id: string | null;
    status: string;
    content_digest: string;
    state_digest: string;
    current_nodes: string[];
    completed_nodes: string[];
    path: string[];
    step_count: number;
    max_steps: number;
    deadline_at: string;
    forked_from_checkpoint_id: string | null;
    error_code: string | null;
    attempts: Array<{
      id: string;
      node_id: string;
      node_type: string;
      attempt: number;
      status: string;
      input_digest: string;
      output_digest: string | null;
    }>;
    checkpoints: Array<{
      id: string;
      sequence: number;
      node_id: string;
      state_digest: string;
      content_digest: string;
    }>;
    waits: WorkflowWait[];
    subworkflows: Array<{
      id: string;
      node_id: string;
      child_run_id: string;
      status: "pending" | "completed" | "failed" | "cancelled" | "timed_out";
      content_digest: string;
      result_state_digest: string | null;
    }>;
    tool_calls: Array<{
      id: string;
      node_id: string;
      child_run_id: string;
      action_id: string;
      tool_id: string;
      status:
        | "pending_approval"
        | "queued"
        | "executing"
        | "reconciliation_required"
        | "reconciling"
        | "completed"
        | "failed"
        | "dead_lettered"
        | "cancelled"
        | "timed_out";
      content_digest: string;
      result_digest: string | null;
    }>;
    tool_simulations: Array<{
      id: string;
      node_id: string;
      simulation_profile_id: string;
      tool_id: string;
      status: "completed";
      content_digest: string;
      result_digest: string;
    }>;
    model_calls: Array<{
      id: string;
      node_id: string;
      model_route_id: string;
      status:
        | "queued"
        | "executing"
        | "reconciliation_required"
        | "reconciling"
        | "completed"
        | "failed"
        | "timed_out";
      request_digest: string;
      content_digest: string;
      output_digest: string | null;
      input_tokens: number | null;
      output_tokens: number | null;
      cost_minor: number | null;
      error_code: string | null;
    }>;
    policy_decisions: Array<{
      id: string;
      node_id: string;
      sequence: number;
      evaluation: number;
      policy_bundle_id: string;
      effect: string;
      reason_code: string;
      input_digest: string;
      policy_digest: string;
      content_digest: string;
      expires_at: string;
    }>;
    replay: null | {
      id: string;
      source_run_id: string;
      source_checkpoint_id: string;
      status: "running" | "matched" | "diverged" | "failed" | "cancelled";
      source_state_digest: string;
      source_path_digest: string;
      replay_state_digest: string | null;
      replay_path_digest: string | null;
      content_digest: string;
    };
  };
  trace_events: TraceEvent[];
  evidence_status: string;
};
