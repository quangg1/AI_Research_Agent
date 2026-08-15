import {
  HttpException,
  Injectable,
  NotFoundException,
  ServiceUnavailableException,
} from "@nestjs/common";
import { InjectQueue } from "@nestjs/bullmq";
import type { Queue } from "bullmq";
import { randomUUID } from "crypto";
import type { Response } from "express";
import { AgentExecutionClient } from "./agent-execution.client";
import { ResumeResearchDto } from "./dto/resume-research.dto";
import { ResearchRepository } from "./research.repository";
import { TERMINAL_STATUSES } from "./research.types";

@Injectable()
export class ResearchService {
  constructor(
    private readonly repository: ResearchRepository,
    private readonly agent: AgentExecutionClient,
    @InjectQueue("research") private readonly queue: Queue,
  ) {}

  examples() {
    return {
      domain: "Applied AI / LLM systems (serving, RAG, agents, eval, fine-tune vs retrieval)",
      queries: [
        "Should we fine-tune a 8B model on weekly runbooks, or use RAG over the same docs?",
        "Compare a vector-only RAG stack vs BM25 plus a cross-encoder reranker for a 50k-chunk internal corpus.",
        "Does RAG always require a vector database?",
        "Should we self-host an 8B FP8 model on one H100 or call a 70B API for 20M tokens/day?",
        "What is continuous batching in vLLM and how does it affect time-to-first-token?",
      ],
    };
  }

  async enqueue(query: string, fresh = false) {
    const recent = await this.repository.query<{ n: number }>(
      `SELECT count(*)::int AS n FROM research_runs
       WHERE created_at > NOW() - INTERVAL '60 seconds'`,
    );
    if ((recent.rows[0]?.n || 0) >= 8) {
      throw new HttpException("Rate limited: too many research runs in the last minute.", 429);
    }
    return this.repository.createRunWithOutbox(query, fresh);
  }

  async resume(id: string, body: ResumeResearchDto) {
    const decision = {
      action: body.action || "approve",
      notes: body.notes || "",
      extra_questions: body.extra_questions || [],
      brief: body.brief || {},
    };
    return this.repository.createResumeWithOutbox(id, decision);
  }

  async get(id: string) {
    const local = await this.repository.query(
      `SELECT * FROM research_runs WHERE id=$1`,
      [id],
    );
    if (!local.rowCount) throw new NotFoundException("run not found");
    const row = local.rows[0];
    return { ...row, agent: row.result_json || undefined };
  }

  async events(id: string, res: Response, lastEventId?: string) {
    await this.get(id);
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache, no-transform");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders();
    res.setTimeout(0);

    let cursor = Number.parseInt(lastEventId || "0", 10);
    if (!Number.isSafeInteger(cursor) || cursor < 0) cursor = 0;
    let closed = false;
    res.on("close", () => {
      closed = true;
    });
    try {
      while (!closed) {
        const events = await this.repository.listEvents(id, cursor);
        for (const event of events.rows) {
          cursor = Number(event.id);
          res.write(`id: ${event.id}\n`);
          res.write(`event: ${event.event_type}\n`);
          res.write(`data: ${JSON.stringify({ type: event.event_type, data: event.payload })}\n\n`);
        }
        const run = await this.repository.query<{ status: string }>(
          `SELECT status FROM research_runs WHERE id=$1`,
          [id],
        );
        const status = run.rows[0]?.status;
        if (
          events.rows.length === 0 &&
          (TERMINAL_STATUSES.includes(status as (typeof TERMINAL_STATUSES)[number]) ||
            status === "awaiting_human" ||
            status === "awaiting_brief")
        ) {
          break;
        }
        res.write(`: heartbeat ${Date.now()}\n\n`);
        await new Promise((resolve) => setTimeout(resolve, 1_000));
      }
    } finally {
      if (!res.writableEnded) res.end();
    }
  }

  async readiness() {
    const failures: string[] = [];
    let agentHealth: Record<string, unknown> | undefined;
    await Promise.all([
      this.repository.ping().catch(() => failures.push("postgres")),
      this.queue.client
        .then((redis) => (redis as unknown as { ping(): Promise<string> }).ping())
        .catch(() => failures.push("redis")),
      this.agent
        .health()
        .then((health) => {
          agentHealth = health;
          if (health.ok !== true) failures.push("agent");
        })
        .catch(() => failures.push("agent")),
    ]);
    if (failures.length) {
      throw new ServiceUnavailableException({
        ok: false,
        service: "kiln-api",
        dependencies: failures,
      });
    }
    return { ok: true, service: "kiln-api", agent: agentHealth };
  }

  async status() {
    return this.agent.health();
  }

  async checkpoints(id: string) {
    const response = await this.agent.fetch(`/v1/runs/${id}/checkpoints`);
    if (!response.ok) throw new NotFoundException("run not found");
    return response.json();
  }

  async listRuns(limit = 30) {
    const rows = await this.repository.query(
      `SELECT id, query, status, pinned, created_at, updated_at
       FROM research_runs ORDER BY created_at DESC LIMIT $1`,
      [limit],
    );
    return { runs: rows.rows };
  }

  async timeline(id: string) {
    const events = await this.repository.listEvents(id, 0, 200);
    return { id, events: events.rows };
  }

  async pin(id: string, pinned = true) {
    const result = await this.repository.query(
      `UPDATE research_runs SET pinned=$2, updated_at=NOW() WHERE id=$1`,
      [id, pinned],
    );
    if (!result.rowCount) throw new NotFoundException("run not found");
    return { id, pinned };
  }

  async corpus() {
    const result = await this.repository.query(
      `SELECT * FROM corpus_documents WHERE active=TRUE
       ORDER BY indexed_at DESC NULLS LAST, fetched_at DESC`,
    );
    const hosts: Record<string, number> = {};
    const tiers: Record<string, number> = {};
    for (const row of result.rows) {
      if (row.host) hosts[String(row.host)] = (hosts[String(row.host)] || 0) + 1;
      if (row.tier) tiers[String(row.tier)] = (tiers[String(row.tier)] || 0) + 1;
    }
    return { documents: result.rows.length, items: result.rows, hosts, tiers };
  }

  async refreshCorpus() {
    const response = await this.agent.fetch("/v1/corpus/refresh", { method: "POST" });
    if (!response.ok) throw new Error(await response.text());
    const refresh = (await response.json()) as Record<string, unknown>;
    const corpus = await this.corpus();
    return { ...refresh, persisted_documents: corpus.documents };
  }

  async knowledgeStats() {
    const response = await this.agent.fetch("/v1/knowledge");
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async knowledgeMatch(query: string) {
    const response = await this.agent.fetch(`/v1/knowledge/match?query=${encodeURIComponent(query)}`);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async servingScenario(body: Record<string, unknown>) {
    return this.runScenario("serving", body);
  }

  async ragScenario(body: Record<string, unknown>) {
    return this.runScenario("rag", body);
  }

  private async runScenario(kind: "serving" | "rag", body: Record<string, unknown>) {
    const response = await this.agent.fetch(`/v1/scenarios/${kind}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await response.text());
    const result = (await response.json()) as Record<string, unknown>;
    await this.repository.query(
      `INSERT INTO scenarios (id, kind, name, inputs_json, outputs_json)
       VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)`,
      [
        randomUUID(),
        kind,
        typeof body.name === "string" ? body.name : `${kind} scenario`,
        JSON.stringify(body),
        JSON.stringify(result),
      ],
    );
    return result;
  }
}
