import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApprovalPanel } from "./ApprovalPanel";

const approval = {
  id: "approval-1",
  run_id: "run-1",
  status: "pending",
  risk: "high",
  reason: "Production side effect requires approval.",
  content_digest: `sha256:${"a".repeat(64)}`,
  request_preview: { tool: "demo.invoice.send", recipient: "op***@example.test" },
  expires_at: "2026-09-01T00:00:00Z",
};

describe("ApprovalPanel", () => {
  it("exposes the exact binding and supports a keyboard approval", async () => {
    const user = userEvent.setup();
    const onDecision = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalPanel approval={approval} busy={false} onDecision={onDecision} />);

    expect(screen.getByText(/cannot authorize changed arguments/i)).toBeInTheDocument();
    await user.tab();
    expect(screen.getByLabelText(/decision justification/i)).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: /deny action/i })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: /approve exact action/i })).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(onDecision).toHaveBeenCalledWith("approve", "Reviewed against production policy");
  });
});

