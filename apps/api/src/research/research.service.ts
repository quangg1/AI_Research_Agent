import {
  BadRequestException,
  ForbiddenException,
  HttpException,
  Injectable,
  NotFoundException,
  ServiceUnavailableException,
} from "@nestjs/common";
import { InjectQueue } from "@nestjs/bullmq";
import type { Queue } from "bullmq";
import { randomBytes, randomUUID } from "crypto";
import type { Response } from "express";
import { AgentExecutionClient } from "./agent-execution.client";
import { ResumeResearchDto } from "./dto/resume-research.dto";
import { LlmCredentialDto, hasVisitorSecrets, llmByokRequired, sanitizeLlm } from "./dto/llm-credential.dto";
import { LlmCredentialVault } from "./llm-credential.vault";
import { ResearchRepository } from "./research.repository";
import { presentRun } from "./present-run";
import { TERMINAL_STATUSES } from "./research.types";
import type { AuthContext } from "../auth/auth.types";
import { isOrgAdmin } from "../auth/auth.types";
import { AuthService } from "../auth/auth.service";
import { BillingService } from "../billing/billing.service";

@Injectable()
export class ResearchService {
  constructor(
    private readonly repository: ResearchRepository,
    private readonly agent: AgentExecutionClient,
    private readonly auth: AuthService,
    private readonly billing: BillingService,
    private readonly llmVault: LlmCredentialVault,
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

  async enqueue(
    query: string,
    fresh = false,
    auth: AuthContext,
    llm?: LlmCredentialDto,
    parentRunId?: string,
  ) {
    const credential = this.requireCredential(llm);
    let effectiveQuery = query;
    if (parentRunId) {
      const parent = await this.requireRun(parentRunId, auth.orgId);
      effectiveQuery = [
        "Follow-up on prior Kiln research.",
        "",
        `Parent question: ${String(parent.query || "").trim()}`,
        "",
        `Follow-up: ${query.trim()}`,
      ].join("\n");
    }
    const recent = await this.repository.query<{ n: number }>(
      `SELECT count(*)::int AS n FROM research_runs
       WHERE org_id=$1 AND created_at > NOW() - INTERVAL '60 seconds'
         AND deleted_at IS NULL`,
      [auth.orgId],
    );
    if ((recent.rows[0]?.n || 0) >= 8) {
      throw new HttpException("Rate limited: too many research runs in the last minute.", 429);
    }
    try {
      await this.billing.assertWithinQuota(auth.orgId);
    } catch (err) {
      const status = (err as { status?: number }).status || 402;
      throw new HttpException((err as Error).message, status);
    }
    const created = await this.repository.createRunWithOutbox(
      effectiveQuery,
      fresh,
      auth.orgId,
      auth.userId,
      undefined,
      credential?.provider,
      credential?.model,
      parentRunId,
    );
    await this.billing.recordUsage(auth.orgId, auth.userId, created.id);
    this.auth.trackEvent(auth.orgId, auth.userId, "run_started", { runId: created.id });
    if (hasVisitorSecrets(credential)) this.llmVault.put(created.executionId, credential);
    return { id: created.id, status: created.status };
  }

  async resume(id: string, body: ResumeResearchDto, auth: AuthContext) {
    await this.requireRun(id, auth.orgId);
    const credential = this.requireCredential(body.llm);
    const decision = {
      action: body.action || "approve",
      notes: body.notes || "",
      extra_questions: body.extra_questions || [],
      brief: body.brief || {},
    };
    if (body.action === "approve" || body.action === "start") {
      this.auth.trackEvent(auth.orgId, auth.userId, "brief_approved", { runId: id });
    }
    const created = await this.repository.createResumeWithOutbox(
      id,
      decision,
      auth.orgId,
      credential?.provider,
      credential?.model,
    );
    if (hasVisitorSecrets(credential)) this.llmVault.put(created.executionId, credential);
    return { id: created.id, status: created.status };
  }

  async get(id: string, auth: AuthContext) {
    const row = await this.requireRun(id, auth.orgId);
    const thread = await this.repository.loadThreadMeta(id, auth.orgId);
    return { ...presentRun(row), thread };
  }

  async evidenceGraph(id: string, auth: AuthContext) {
    const row = await this.requireRun(id, auth.orgId);
    const stored = await this.repository.loadEvidenceGraph(id, auth.orgId);
    if (stored) return stored;
    const presented = presentRun(row);
    return presented.evidence_graph || { claims: [], sources: [], edges: [] };
  }

  async cancel(id: string, auth: AuthContext) {
    const result = await this.repository.cancelRun(id, auth.orgId);
    if (!result) throw new NotFoundException("run not found");
    this.auth.trackEvent(auth.orgId, auth.userId, "run_cancelled", { runId: id });
    return result;
  }

  async softDelete(id: string, auth: AuthContext) {
    const ok = await this.repository.softDeleteRun(id, auth.orgId);
    if (!ok) throw new NotFoundException("run not found");
    return { id, deleted: true };
  }

  async duplicate(id: string, auth: AuthContext, llm?: LlmCredentialDto) {
    const row = await this.requireRun(id, auth.orgId);
    return this.enqueue(String(row.query), true, auth, llm);
  }

  async events(id: string, res: Response, lastEventId: string | undefined, auth: AuthContext) {
    await this.requireRun(id, auth.orgId);
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
          `SELECT status FROM research_runs WHERE id=$1 AND org_id=$2`,
          [id, auth.orgId],
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
        .ready()
        .then((health) => {
          agentHealth = health;
          if (health.ok !== true) failures.push("agent");
        })
        .catch((err: Error) => {
          failures.push(err.message.startsWith("agent_auth:") ? "agent_auth" : "agent");
        }),
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

  async checkpoints(id: string, auth: AuthContext) {
    await this.requireRun(id, auth.orgId);
    const response = await this.agent.fetch(`/v1/runs/${id}/checkpoints`);
    if (!response.ok) throw new NotFoundException("run not found");
    return response.json();
  }

  async listRuns(
    auth: AuthContext,
    opts: {
      limit?: number;
      cursor?: string;
      q?: string;
      status?: string;
      archived?: boolean;
    } = {},
  ) {
    const limit = Math.min(Math.max(opts.limit || 30, 1), 100);
    const values: unknown[] = [auth.orgId];
    const clauses = [`org_id=$1`, `deleted_at IS NULL`];
    if (!opts.archived) clauses.push(`archived=FALSE`);
    if (opts.status) {
      values.push(opts.status);
      clauses.push(`status=$${values.length}`);
    }
    if (opts.q) {
      values.push(`%${opts.q}%`);
      clauses.push(`(query ILIKE $${values.length} OR COALESCE(title,'') ILIKE $${values.length})`);
    }
    if (opts.cursor) {
      values.push(opts.cursor);
      clauses.push(`created_at < $${values.length}`);
    }
    values.push(limit);
    const rows = await this.repository.query(
      `SELECT id, query, title, status, pinned, archived, created_at, updated_at, created_by
       FROM research_runs
       WHERE ${clauses.join(" AND ")}
       ORDER BY created_at DESC
       LIMIT $${values.length}`,
      values,
    );
    const nextCursor =
      rows.rows.length === limit
        ? String(rows.rows[rows.rows.length - 1].created_at)
        : null;
    return { runs: rows.rows, next_cursor: nextCursor };
  }

  async timeline(id: string, auth: AuthContext) {
    await this.requireRun(id, auth.orgId);
    const events = await this.repository.listEvents(id, 0, 200);
    const readable = events.rows.map((ev) => ({
      ...ev,
      summary: this.summarizeEvent(String(ev.event_type), ev.payload),
    }));
    return { id, events: readable };
  }

  async pin(id: string, pinned = true, auth: AuthContext) {
    const result = await this.repository.query(
      `UPDATE research_runs SET pinned=$3, updated_at=NOW()
       WHERE id=$1 AND org_id=$2 AND deleted_at IS NULL`,
      [id, auth.orgId, pinned],
    );
    if (!result.rowCount) throw new NotFoundException("run not found");
    return { id, pinned };
  }

  async rename(id: string, title: string, auth: AuthContext) {
    const result = await this.repository.query(
      `UPDATE research_runs SET title=$3, updated_at=NOW()
       WHERE id=$1 AND org_id=$2 AND deleted_at IS NULL`,
      [id, auth.orgId, title.slice(0, 200)],
    );
    if (!result.rowCount) throw new NotFoundException("run not found");
    return { id, title: title.slice(0, 200) };
  }

  async archive(id: string, archived = true, auth: AuthContext) {
    const result = await this.repository.query(
      `UPDATE research_runs SET archived=$3, updated_at=NOW()
       WHERE id=$1 AND org_id=$2 AND deleted_at IS NULL`,
      [id, auth.orgId, archived],
    );
    if (!result.rowCount) throw new NotFoundException("run not found");
    return { id, archived };
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

  async refreshCorpus(auth: AuthContext) {
    if (!isOrgAdmin(auth.role)) {
      throw new ForbiddenException("Organization admin role required to refresh corpus");
    }
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
    const response = await this.agent.fetch(
      `/v1/knowledge/match?query=${encodeURIComponent(query)}`,
    );
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async servingScenario(body: Record<string, unknown>, auth: AuthContext) {
    return this.runScenario("serving", body, auth);
  }

  async ragScenario(body: Record<string, unknown>, auth: AuthContext) {
    return this.runScenario("rag", body, auth);
  }

  async createShare(runId: string, auth: AuthContext, expiresInDays = 14) {
    await this.requireRun(runId, auth.orgId);
    const token = randomBytes(24).toString("base64url");
    const id = randomUUID();
    const expiresAt = new Date(Date.now() + expiresInDays * 86400_000);
    await this.repository.query(
      `INSERT INTO run_shares (id, run_id, org_id, token, permission, expires_at, created_by)
       VALUES ($1, $2, $3, $4, 'read', $5, $6)`,
      [id, runId, auth.orgId, token, expiresAt.toISOString(), auth.userId],
    );
    return {
      id,
      token,
      url_path: `/share/${token}`,
      expires_at: expiresAt.toISOString(),
      permission: "read",
    };
  }

  async revokeShare(shareId: string, auth: AuthContext) {
    const result = await this.repository.query(
      `UPDATE run_shares SET revoked_at=NOW()
       WHERE id=$1 AND org_id=$2 AND revoked_at IS NULL`,
      [shareId, auth.orgId],
    );
    if (!result.rowCount) throw new NotFoundException("share not found");
    return { id: shareId, revoked: true };
  }

  async getSharedRun(token: string) {
    const share = await this.repository.query<{
      run_id: string;
      org_id: string;
      expires_at: Date | null;
      revoked_at: Date | null;
    }>(
      `SELECT run_id, org_id, expires_at, revoked_at FROM run_shares WHERE token=$1`,
      [token],
    );
    if (!share.rowCount) throw new NotFoundException("share not found");
    const row = share.rows[0];
    if (row.revoked_at) throw new ForbiddenException("share revoked");
    if (row.expires_at && new Date(row.expires_at).getTime() < Date.now()) {
      throw new ForbiddenException("share expired");
    }
    const run = await this.repository.query(
      `SELECT id, query, title, status, result_json, interrupt_payload, metrics_json, created_at
       FROM research_runs WHERE id=$1 AND org_id=$2 AND deleted_at IS NULL`,
      [row.run_id, row.org_id],
    );
    if (!run.rowCount) throw new NotFoundException("run not found");
    return presentRun(run.rows[0], { share: { permission: "read" } });
  }

  async exportOrg(auth: AuthContext) {
    if (!isOrgAdmin(auth.role)) {
      throw new ForbiddenException("Organization admin role required");
    }
    const runs = await this.repository.query(
      `SELECT id, query, title, status, metrics_json, result_json, created_at, updated_at
       FROM research_runs WHERE org_id=$1 AND deleted_at IS NULL
       ORDER BY created_at DESC LIMIT 1000`,
      [auth.orgId],
    );
    return {
      org_id: auth.orgId,
      exported_at: new Date().toISOString(),
      runs: runs.rows.map((r) => ({
        id: r.id,
        query: r.query,
        title: r.title,
        status: r.status,
        created_at: r.created_at,
        report: (r.result_json as { values?: { report?: unknown } } | null)?.values?.report,
      })),
    };
  }

  async listNotifications(auth: AuthContext) {
    const rows = await this.repository.query(
      `SELECT id, run_id, kind, title, body, read_at, created_at
       FROM notifications
       WHERE org_id=$1 AND (user_id IS NULL OR user_id=$2)
       ORDER BY created_at DESC LIMIT 50`,
      [auth.orgId, auth.userId],
    );
    return { notifications: rows.rows };
  }

  async markNotificationRead(id: string, auth: AuthContext) {
    await this.repository.query(
      `UPDATE notifications SET read_at=NOW()
       WHERE id=$1 AND org_id=$2 AND (user_id IS NULL OR user_id=$3)`,
      [id, auth.orgId, auth.userId],
    );
    return { id, read: true };
  }

  private async runScenario(
    kind: "serving" | "rag",
    body: Record<string, unknown>,
    auth: AuthContext,
  ) {
    const response = await this.agent.fetch(`/v1/scenarios/${kind}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await response.text());
    const result = (await response.json()) as Record<string, unknown>;
    await this.repository.query(
      `INSERT INTO scenarios (id, kind, name, inputs_json, outputs_json, org_id, created_by)
       VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7)`,
      [
        randomUUID(),
        kind,
        typeof body.name === "string" ? body.name : `${kind} scenario`,
        JSON.stringify(body),
        JSON.stringify(result),
        auth.orgId,
        auth.userId,
      ],
    );
    return result;
  }

  async status() {
    const health = (await this.agent.health()) as Record<string, unknown>;
    return {
      ...health,
      byok_required: llmByokRequired() || health.byok_required === true,
      llm_mode: llmByokRequired() ? "byok" : health.llm_mode || "platform",
    };
  }

  private requireCredential(llm?: LlmCredentialDto) {
    const credential = sanitizeLlm(llm);
    if (!credential) {
      throw new BadRequestException("Select Gemini, OpenAI, or Grok.");
    }
    if (llmByokRequired() && !hasVisitorSecrets(credential)) {
      throw new BadRequestException(
        "A model API key is required. Paste your Gemini, OpenAI, or Grok key. Kiln never stores visitor keys.",
      );
    }
    return credential;
  }

  private async requireRun(id: string, orgId: string) {
    const row = await this.repository.getRunForOrg(id, orgId);
    if (!row) throw new NotFoundException("run not found");
    return row;
  }

  private summarizeEvent(eventType: string, payload: unknown): string {
    if (eventType.startsWith("status:")) return `Status → ${eventType.slice(7)}`;
    if (eventType === "interrupt") return "Waiting for human review";
    if (eventType === "update") {
      const node = (payload as { current_node?: string; snapshot?: { current_node?: string } })
        ?.current_node ||
        (payload as { snapshot?: { current_node?: string } })?.snapshot?.current_node;
      return node ? `Progress: ${node}` : "Run updated";
    }
    if (eventType === "heartbeat") return "Heartbeat";
    if (eventType === "error") return "Execution error";
    return eventType;
  }
}
