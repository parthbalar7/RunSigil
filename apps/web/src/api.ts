import type { RunDetail, WorkspaceContext } from "./types";

export type ApiConfig = { baseUrl: string; apiKey: string };

export class ApiProblem extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(config: ApiConfig, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${config.baseUrl.replace(/\/$/, "")}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${config.apiKey}`,
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    throw new ApiProblem(
      String(body.message ?? "RunSigil request failed."),
      String(body.code ?? "RUNSIGIL_HTTP_ERROR"),
      response.status,
    );
  }
  return body as T;
}

export const api = {
  context: (config: ApiConfig) => request<WorkspaceContext>(config, "/v1/context"),
  getRun: (config: ApiConfig, runId: string) => request<RunDetail>(config, `/v1/runs/${runId}`),
  startRun: (
    config: ApiConfig,
    input: {
      project_id: string;
      environment_id: string;
      agent_id: string;
      recipient: string;
      amount_cents: number;
      description: string;
      idempotency_key: string;
      simulate_outcome: string;
    },
  ) => request<RunDetail>(config, "/v1/runs", { method: "POST", body: JSON.stringify(input) }),
  decideApproval: (
    config: ApiConfig,
    approvalId: string,
    input: { content_digest: string; decision: "approve" | "deny"; reason: string },
  ) =>
    request<RunDetail>(config, `/v1/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
};

