import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RunPath, Timeline } from "./RunPath";
import type { RunDetail } from "../types";

const run: RunDetail = {
  id: "run-1",
  run_kind: "governed_action",
  status: "waiting_for_approval",
  project_id: "project-1",
  environment_id: "env-1",
  agent_id: "agent-1",
  active_node: "human-approval",
  input_digest: `sha256:${"b".repeat(64)}`,
  created_at: "2026-08-31T00:00:00Z",
  started_at: null,
  completed_at: null,
  error_code: null,
  action: null,
  approval: null,
  workflow: null,
  trace_events: [],
  evidence_status: "pending",
};

describe("Run correlation", () => {
  it("offers an accessible textual workflow and node selection", async () => {
    const user = userEvent.setup();
    const select = vi.fn();
    render(<RunPath run={run} selectedNode={null} onSelect={select} />);
    const approvalNode = screen.getByRole("button", { name: /human approval: active/i });
    await user.click(approvalNode);
    expect(select).toHaveBeenCalledWith("human-approval");
  });

  it("selecting a trace event reports its correlated node", async () => {
    const user = userEvent.setup();
    const select = vi.fn();
    const event = {
      id: "event-1",
      node_id: "policy-check",
      span_id: "span-1",
      event_type: "guardrail.decision",
      status: "require_approval",
      sequence: 1,
      attributes: {},
      created_at: "2026-08-31T00:00:00Z",
    };
    render(<Timeline events={[event]} selectedEvent={null} onSelect={select} />);
    await user.click(screen.getByRole("button", { name: /guardrail/i }));
    expect(select).toHaveBeenCalledWith(event);
  });

  it("renders and selects the actual durable workflow trajectory", async () => {
    const user = userEvent.setup();
    const select = vi.fn();
    const workflowRun: RunDetail = {
      ...run,
      run_kind: "workflow",
      status: "queued",
      active_node: "route",
      workflow: {
        id: "execution-1",
        workflow_version_id: "version-1",
        deployment_id: "deployment-1",
        execution_mode: "live",
        simulation_profile_id: null,
        status: "queued",
        content_digest: `sha256:${"c".repeat(64)}`,
        state_digest: `sha256:${"d".repeat(64)}`,
        current_nodes: ["route"],
        completed_nodes: ["start"],
        path: ["start"],
        step_count: 1,
        max_steps: 20,
        deadline_at: "2026-08-31T00:05:00Z",
        forked_from_checkpoint_id: null,
        error_code: null,
        attempts: [{
          id: "attempt-1",
          node_id: "start",
          node_type: "input",
          attempt: 1,
          status: "completed",
          input_digest: `sha256:${"d".repeat(64)}`,
          output_digest: `sha256:${"d".repeat(64)}`,
        }],
        checkpoints: [],
        waits: [{
          id: "wait-1",
          run_id: "run-1",
          workflow_execution_id: "execution-1",
          node_id: "route",
          sequence: 2,
          wait_type: "approval",
          status: "pending",
          resolution: null,
          content_digest: `sha256:${"e".repeat(64)}`,
          state_digest: `sha256:${"d".repeat(64)}`,
          request_metadata: { risk: "high", reason_code: "operator_review" },
          event_key: null,
          due_at: null,
          expires_at: "2026-08-31T00:04:00Z",
          response_digest: null,
          resolved_by: null,
          resolved_at: null,
          created_at: "2026-08-31T00:00:01Z",
        }],
        subworkflows: [],
        tool_calls: [],
        tool_simulations: [],
        model_calls: [],
        policy_decisions: [],
        replay: null,
      },
    };
    const { rerender } = render(
      <RunPath run={workflowRun} selectedNode={null} onSelect={select} />,
    );
    await user.click(screen.getByRole("button", { name: /route: waiting.*approval wait.*pending/i }));
    expect(select).toHaveBeenCalledWith("route");
    expect(screen.getByLabelText(/executed workflow trajectory/i)).toBeInTheDocument();

    const toolRun: RunDetail = {
      ...workflowRun,
      workflow: workflowRun.workflow && {
        ...workflowRun.workflow,
        waits: [],
        tool_calls: [{
          id: "tool-call-1",
          node_id: "route",
          child_run_id: "child-run-1",
          action_id: "action-1",
          tool_id: "tool-1",
          status: "pending_approval",
          content_digest: `sha256:${"f".repeat(64)}`,
          result_digest: null,
        }],
      },
    };
    rerender(<RunPath run={toolRun} selectedNode={null} onSelect={select} />);
    expect(
      screen.getByRole("button", {
        name: /route: waiting.*governed tool.*pending approval/i,
      }),
    ).toBeInTheDocument();
  });
});
