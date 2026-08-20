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
    );

    expect(result).toMatchObject({ status: "queued", executionVersion: 1 });
    expect(client.query.mock.calls.map(([sql]) => String(sql).trim().split(/\s+/)[0])).toEqual([
      "BEGIN",
      "INSERT",
      "INSERT",
      "INSERT",
      "COMMIT",
    ]);
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
});
