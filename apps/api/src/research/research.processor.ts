import { OnWorkerEvent, Processor, WorkerHost } from "@nestjs/bullmq";
import { Job } from "bullmq";
import { AgentExecutionClient } from "./agent-execution.client";
import { hasVisitorSecrets, llmByokRequired, scrubText } from "./dto/llm-credential.dto";
import { LlmCredentialVault } from "./llm-credential.vault";
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
    private readonly llmVault: LlmCredentialVault,
  ) {
    super();
  }

  async process(job: Job<ExecuteJobPayload>): Promise<void> {
    if (job.name !== "execute") throw new Error(`Unsupported research job: ${job.name}`);
    const payload: ExecuteJobPayload = { ...job.data };
    if (!payload.orgId) {
      const tenancy = await this.repository.getRunTenancy(payload.runId);
      if (tenancy) {
        payload.orgId = tenancy.orgId;
        payload.userId = payload.userId || tenancy.userId || undefined;
      }
    }
    const active = await this.repository.setCurrentJob(payload, String(job.id));
    if (!active) return;

    const userLlm = this.llmVault.peek(job.data.executionId);
    if (llmByokRequired() && !hasVisitorSecrets(userLlm)) {
      await this.repository.failExecution(
        job.data,
        "Your model API key is no longer in memory. Kiln does not store keys — start or resume again and paste the key.",
      );
      return;
    }
    const llm = hasVisitorSecrets(userLlm)
      ? userLlm
      : job.data.llmProvider
        ? { provider: job.data.llmProvider, model: job.data.llmModel }
        : undefined;

    const attempt = job.attemptsMade + 1;
    let finished = false;
    try {
      for await (const frame of this.agent.execute(payload, llm)) {
        if (frame.type === "error") {
          if (frame.retryable !== false) {
            throw new Error(frame.error || "Agent execution failed");
          }
          await this.repository.failExecution(
            payload,
            scrubText(frame.error || "Agent execution failed"),
          );
          finished = true;
          break;
        }
        const applied = await this.repository.applyExecutionFrame(payload, frame, attempt);
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
    } finally {
      if (finished) this.llmVault.release(job.data.executionId);
    }
  }

  @OnWorkerEvent("failed")
  async onFailed(job: Job | undefined, error: Error) {
    if (!job) return;
    const attempts = Number(job.opts.attempts || 1);
    if (job.attemptsMade >= attempts) {
      this.llmVault.release((job.data as ExecuteJobPayload).executionId);
      await this.repository.failExecution(
        job.data as ExecuteJobPayload,
        `Agent execution failed after ${attempts} attempts: ${scrubText(error.message)}`,
      );
    }
  }
}
