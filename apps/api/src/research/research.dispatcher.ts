import { Injectable, Logger, OnModuleDestroy, OnModuleInit } from "@nestjs/common";
import { InjectQueue } from "@nestjs/bullmq";
import { Queue } from "bullmq";
import { ResearchRepository } from "./research.repository";

@Injectable()
export class ResearchDispatcher implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(ResearchDispatcher.name);
  private stopped = false;
  private loop?: Promise<void>;

  constructor(
    @InjectQueue("research") private readonly queue: Queue,
    private readonly repository: ResearchRepository,
  ) {}

  onModuleInit() {
    this.loop = this.run();
  }

  async onModuleDestroy() {
    this.stopped = true;
    await this.loop;
  }

  private async run() {
    const pollMs = Number(process.env.OUTBOX_POLL_INTERVAL_MS || 500);
    while (!this.stopped) {
      try {
        let dispatched = false;
        do {
          dispatched = await this.repository.dispatchNext(async (record) => {
            const jobId = `research:${record.run_id}:execution:${record.execution_version}`;
            await this.queue.add("execute", record.payload, {
              jobId,
              attempts: Number(process.env.RESEARCH_JOB_ATTEMPTS || 4),
              backoff: { type: "exponential", delay: 2_000 },
              removeOnComplete: 100,
              removeOnFail: 100,
            });
          });
        } while (dispatched && !this.stopped);
      } catch (error) {
        this.logger.error(`Outbox dispatch failed: ${(error as Error).message}`);
      }
      if (!this.stopped) await new Promise((resolve) => setTimeout(resolve, pollMs));
    }
  }
}
