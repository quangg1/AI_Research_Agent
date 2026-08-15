import { OnWorkerEvent, Processor, WorkerHost } from "@nestjs/bullmq";
import { Job } from "bullmq";
import { AgentExecutionClient } from "./agent-execution.client";
import { ResearchRepository } from "./research.repository";
import { ExecuteJobPayload } from "./research.types";

@Processor("research", {
  lockDuration: 300_000,
  stalledInterval: 30_000,
  maxStalledCount: 2,
})
export class ResearchProcessor extends WorkerHost {
  constructor(
    private readonly agent: AgentExecutionClient,
    private readonly repository: ResearchRepository,
  ) {
    super();
  }

  async process(job: Job<ExecuteJobPayload>): Promise<void> {
    if (job.name !== "execute") throw new Error(`Unsupported research job: ${job.name}`);
    const active = await this.repository.setCurrentJob(job.data, String(job.id));
    if (!active) return;

    const attempt = job.attemptsMade + 1;
    let finished = false;
    for await (const frame of this.agent.execute(job.data)) {
      if (frame.type === "error") {
        if (frame.retryable !== false) {
          throw new Error(frame.error || "Agent execution failed");
        }
        await this.repository.failExecution(job.data, frame.error || "Agent execution failed");
        finished = true;
        break;
      }
      const applied = await this.repository.applyExecutionFrame(job.data, frame, attempt);
      if (!applied) return;
      if (frame.type === "heartbeat" || frame.type === "update") {
        await job.updateProgress({
          sequence: frame.sequence,
          current_node: frame.current_node || frame.snapshot?.current_node,
        });
      }
      if (frame.type === "interrupt" || frame.type === "terminal") {
        finished = true;
        break;
      }
    }
    if (!finished) throw new Error("Agent execution stream ended before a terminal frame");
  }

  @OnWorkerEvent("failed")
  async onFailed(job: Job | undefined, error: Error) {
    if (!job) return;
    const attempts = Number(job.opts.attempts || 1);
    if (job.attemptsMade >= attempts) {
      await this.repository.failExecution(
        job.data as ExecuteJobPayload,
        `Agent execution failed after ${attempts} attempts: ${error.message}`,
      );
    }
  }
}
