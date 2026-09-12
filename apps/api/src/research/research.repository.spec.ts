import type { Pool } from "pg";
import { ResearchRepository } from "./research.repository";
import type { ExecuteJobPayload, ExecutionFrame } from "./research.types";

describe("ResearchRepository", () => {
  test("creates the run, initial event, and outbox in one transaction", async () => {
    const client = {
      query: jest.fn().mockResolvedValue({ rowCount: 1, rows: [] }),
      release: jest.fn(),
    };
    const pool = { connect: jest.fn().mockResolvedValue(client) } as unknown as Pool;
    const repository = new ResearchRepository(pool);

    const result = await repository.createRunWithOutbox(
      "A sufficiently detailed query",
      true,
      "org_default",
      "user_dev",
      undefined,
      "gemini",
    );

    expect(result).toMatchObject({ status: "queued", executionVersion: 1 });
    const outboxArgs = client.query.mock.calls[3][1] as unknown[];
    const outboxPayload = JSON.parse(String(outboxArgs[2]));
    expect(outboxPayload.llmProvider).toBe("gemini");
    expect(outboxPayload).not.toHaveProperty("llm");
    expect(JSON.stringify(outboxPayload)).not.toMatch(/apiKey|sk-/);
    expect(client.query.mock.calls.map(([sql]) => String(sql).trim().split(/\s+/)[0])).toEqual([
      "BEGIN",
      "INSERT",
      "INSERT",
      "INSERT",
      "COMMIT",
    ]);
    expect(client.query.mock.calls[1][0]).toContain("$1::uuid");
    expect(client.query.mock.calls[1][0]).toContain("$1::text");
    expect(client.query.mock.calls[3][0]).toContain("research_dispatch_outbox");
    expect(client.release).toHaveBeenCalled();
  });

  test("does not persist an event when the execution version guard misses", async () => {
    const client = {
      query: jest
        .fn()
        .mockResolvedValueOnce({ rowCount: 1, rows: [] }) // BEGIN
        .mockResolvedValueOnce({ rowCount: 0, rows: [] }) // SELECT lock miss
        .mockResolvedValue({ rowCount: 1, rows: [] }),
      release: jest.fn(),
    };
    const pool = { connect: jest.fn().mockResolvedValue(client) } as unknown as Pool;
    const repository = new ResearchRepository(pool);
    const payload: ExecuteJobPayload = {
      kind: "start",
      runId: "run",
      executionId: "execution",
      executionVersion: 2,
      query: "query",
    };
    const frame = {
      type: "heartbeat",
      runId: "run",
      executionId: "execution",
      sequence: 1,
    } as ExecutionFrame;

    await expect(repository.applyExecutionFrame(payload, frame, 1)).resolves.toBe(false);
    expect(client.query.mock.calls[1][0]).toContain("FOR UPDATE");
  });

  test("heartbeat with stale interrupt does not flip status back to awaiting_human", async () => {
    const updateArgs: unknown[][] = [];
    const client = {
      query: jest.fn().mockImplementation(async (sql: string, params?: unknown[]) => {
        const text = String(sql);
        if (text.startsWith("BEGIN") || text.startsWith("COMMIT")) {
          return { rowCount: 1, rows: [] };
        }
        if (text.includes("FOR UPDATE")) {
          return { rowCount: 1, rows: [{ status: "running", org_id: "org", created_by: null }] };
        }
        if (text.includes("UPDATE research_runs SET")) {
          updateArgs.push(params || []);
          return { rowCount: 1, rows: [] };
        }
        return { rowCount: 1, rows: [] };
      }),
      release: jest.fn(),
    };
    const pool = { connect: jest.fn().mockResolvedValue(client) } as unknown as Pool;
    const repository = new ResearchRepository(pool);
    const payload: ExecuteJobPayload = {
      kind: "resume",
      runId: "run",
      executionId: "execution",
      executionVersion: 2,
      decision: { action: "start" },
    };
    const frame = {
      type: "heartbeat",
      runId: "run",
      executionId: "execution",
      sequence: 1,
      snapshot: {
        status: "awaiting_human",
        current_node: "briefing",
        interrupt: { type: "research_brief" },
      },
    } as ExecutionFrame;

    await expect(repository.applyExecutionFrame(payload, frame, 1)).resolves.toBe(true);
    expect(updateArgs[0][3]).toBe("running");
    const stored = JSON.parse(String(updateArgs[0][5]));
    expect(stored.status).toBe("running");
    expect(stored.interrupt).toBeUndefined();
    expect(updateArgs[0][6]).toBeNull();
  });

  test("soft-deleting a run also deactivates its cached knowledge_records answer", async () => {
    const client = {
      query: jest.fn().mockImplementation(async (sql: string) => {
        const text = String(sql);
        if (text.startsWith("BEGIN") || text.startsWith("COMMIT")) {
          return { rowCount: 1, rows: [] };
        }
        if (text.includes("UPDATE research_runs")) {
          return { rowCount: 1, rows: [{ thread_id: "thread-123" }] };
        }
        if (text.includes("UPDATE knowledge_records")) {
          return { rowCount: 1, rows: [] };
        }
        return { rowCount: 1, rows: [] };
      }),
      release: jest.fn(),
    };
    const pool = { connect: jest.fn().mockResolvedValue(client) } as unknown as Pool;
    const repository = new ResearchRepository(pool);

    await expect(repository.softDeleteRun("run-1", "org_default")).resolves.toBe(true);

    const knowledgeCall = client.query.mock.calls.find(([sql]) =>
      String(sql).includes("UPDATE knowledge_records"),
    );
    expect(knowledgeCall).toBeDefined();
    expect(knowledgeCall?.[1]).toEqual(["thread-123"]);
    expect(String(knowledgeCall?.[0])).toContain("active=FALSE");
  });

  test("soft-delete on a run that doesn't exist never touches knowledge_records", async () => {
    const client = {
      query: jest.fn().mockImplementation(async (sql: string) => {
        const text = String(sql);
        if (text.startsWith("BEGIN") || text.startsWith("COMMIT")) {
          return { rowCount: 1, rows: [] };
        }
        if (text.includes("UPDATE research_runs")) {
          return { rowCount: 0, rows: [] };
        }
        return { rowCount: 1, rows: [] };
      }),
      release: jest.fn(),
    };
    const pool = { connect: jest.fn().mockResolvedValue(client) } as unknown as Pool;
    const repository = new ResearchRepository(pool);

    await expect(repository.softDeleteRun("missing", "org_default")).resolves.toBe(false);

    const knowledgeCall = client.query.mock.calls.find(([sql]) =>
      String(sql).includes("knowledge_records"),
    );
    expect(knowledgeCall).toBeUndefined();
  });
});
