import { presentRun } from "./present-run";

describe("presentRun", () => {
  test("promotes persisted HITL status over a running snapshot", () => {
    const presented = presentRun({
      id: "run-1",
      status: "awaiting_human",
      result_json: { status: "running", current_node: "briefing" },
      interrupt_payload: { type: "research_brief", title: "Research plan" },
    });
    expect(presented.agent).toMatchObject({
      status: "awaiting_human",
      current_node: "briefing",
      interrupt: { type: "research_brief", title: "Research plan" },
    });
  });

  test("keeps the snapshot when the run is still executing", () => {
    const presented = presentRun({
      id: "run-2",
      status: "running",
      result_json: { status: "running", current_node: "planner" },
      interrupt_payload: null,
    });
    expect(presented.agent).toMatchObject({ status: "running", current_node: "planner" });
    expect(presented.agent?.interrupt).toBeUndefined();
  });

  test("surfaces the evidence graph from report metrics", () => {
    const presented = presentRun({
      id: "run-4",
      status: "completed",
      result_json: {
        status: "completed",
        values: { report: { metrics: { evidence_graph: { claims: [{ id: "C1" }] } } } },
      },
    });
    expect(presented.evidence_graph).toEqual({ claims: [{ id: "C1" }] });
  });

  test("does not resurrect the brief gate from current_node alone", () => {
    const presented = presentRun({
      id: "run-6",
      status: "running",
      result_json: { status: "running", current_node: "briefing" },
      interrupt_payload: null,
    });
    expect(presented.status).toBe("running");
    expect(presented.agent?.interrupt).toBeUndefined();
  });

  test("keeps the review gate when a heartbeat flipped status to running", () => {
    const presented = presentRun({
      id: "run-5",
      status: "running",
      result_json: {
        status: "running",
        current_node: "hitl",
        hint: "Waiting for your review",
      },
      interrupt_payload: { type: "approve_report", claims_preview: [{ id: "C1" }] },
    });
    expect(presented.status).toBe("awaiting_human");
    expect(presented.agent).toMatchObject({
      status: "awaiting_human",
      current_node: "hitl",
      interrupt: { type: "approve_report" },
    });
  });

  test("drops a stale HITL interrupt after resume starts", () => {
    const presented = presentRun({
      id: "run-3",
      status: "queued",
      result_json: {
        status: "awaiting_human",
        current_node: "briefing",
        interrupt: { type: "research_brief" },
      },
      interrupt_payload: null,
    });
    expect(presented.status).toBe("queued");
    expect(presented.agent).toMatchObject({ status: "queued", current_node: "briefing" });
    expect(presented.agent?.interrupt).toBeUndefined();
  });

  test("keeps credits pause when heartbeat flipped status to running mid-report", () => {
    const presented = presentRun({
      id: "run-8",
      status: "running",
      result_json: {
        status: "running",
        current_node: "report",
        hint: "Writing the long memo",
      },
      interrupt_payload: {
        type: "credits_exhausted",
        message: "All configured providers are out of credits",
      },
    });
    expect(presented.status).toBe("awaiting_human");
    expect(presented.agent).toMatchObject({
      status: "awaiting_human",
      interrupt: { type: "credits_exhausted" },
    });
  });
});
