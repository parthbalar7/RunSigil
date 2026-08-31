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

export type RunDetail = {
  id: string;
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
  trace_events: TraceEvent[];
  evidence_status: string;
};

