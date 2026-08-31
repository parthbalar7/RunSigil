import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RunPath, Timeline } from "./RunPath";
import type { RunDetail } from "../types";

const run: RunDetail = {
  id: "run-1",
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
});

