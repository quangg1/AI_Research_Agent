import { ResearchDispatcher } from "./research.dispatcher";
import type { ResearchRepository } from "./research.repository";

describe("ResearchDispatcher", () => {
  test("publishes a deterministic execute job from the outbox", async () => {
    const queue = { add: jest.fn().mockResolvedValue({}) };
    let dispatcher!: ResearchDispatcher;
    const repository = {
      dispatchNext: jest.fn(async (publish: (record: any) => Promise<void>) => {
        await publish({
          id: "1",
          run_id: "run-1",
          execution_id: "execution-1",
          execution_version: 3,
          job_name: "execute",
          payload: {
            kind: "resume",
            runId: "run-1",
            executionId: "execution-1",
            executionVersion: 3,
            decision: {},
          },
          attempts: 0,
        });
        (dispatcher as any).stopped = true;
        return true;
      }),
    };
    dispatcher = new ResearchDispatcher(queue as any, repository as unknown as ResearchRepository);

    await (dispatcher as any).run();

    expect(queue.add).toHaveBeenCalledWith(
      "execute",
      expect.objectContaining({ runId: "run-1", executionVersion: 3 }),
      expect.objectContaining({ jobId: "research-run-1-v3" }),
    );
  });
});
