import { Injectable } from "@nestjs/common";
import { ExecuteJobPayload, ExecutionFrame } from "./research.types";
import { agentLlmPayload, scrubObj, scrubText, type LlmCredentialDto } from "./dto/llm-credential.dto";

const FRAME_TYPES = new Set(["update", "heartbeat", "interrupt", "terminal", "error"]);

function normalizeAgentBaseUrl(raw: string): string {
  const trimmed = raw.trim().replace(/\/+$/, "");
  if (!trimmed) return "http://localhost:8000";
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

@Injectable()
export class AgentExecutionClient {
  private readonly baseUrl = normalizeAgentBaseUrl(process.env.AGENT_BASE_URL || "http://localhost:8000");
  private readonly idleTimeoutMs = Number(process.env.AGENT_STREAM_IDLE_TIMEOUT_MS || 120_000);

  private agentKey(): string {
    const override = (process.env.API_TO_AGENT_KEY || "").trim();
    if (override) return override;
    return (process.env.AGENT_SHARED_KEY || "").trim();
  }

  private headers(contentType = false): Record<string, string> {
    const key = this.agentKey();
    return {
      ...(contentType ? { "Content-Type": "application/json" } : {}),
      ...(key ? { "X-Agent-Key": key } : {}),
    };
  }

  async *execute(
    payload: ExecuteJobPayload,
    llm?: LlmCredentialDto,
  ): AsyncGenerator<ExecutionFrame> {
    const body =
      payload.kind === "start"
        ? {
            kind: "start",
            runId: payload.runId,
            executionId: payload.executionId,
            query: payload.query,
            fresh: payload.fresh === true,
            ...(payload.orgId ? { orgId: payload.orgId } : {}),
            ...(payload.userId ? { userId: payload.userId } : {}),
            ...(llm ? { llm: agentLlmPayload(llm) } : {}),
          }
        : {
            kind: "resume",
            runId: payload.runId,
            executionId: payload.executionId,
            decision: payload.decision || {},
            ...(payload.orgId ? { orgId: payload.orgId } : {}),
            ...(payload.userId ? { userId: payload.userId } : {}),
            ...(llm ? { llm: agentLlmPayload(llm) } : {}),
          };
    const controller = new AbortController();
    const response = await fetch(`${this.baseUrl}/internal/v1/executions/stream`, {
      method: "POST",
      headers: this.headers(true),
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      throw new Error(
        `Agent execution stream failed (${response.status}): ${scrubText(await response.text())}`,
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffered = "";
    try {
      while (true) {
        let timer: ReturnType<typeof setTimeout> | undefined;
        const timeout = new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            controller.abort();
            reject(new Error(`Agent execution stream idle for ${this.idleTimeoutMs}ms`));
          }, this.idleTimeoutMs);
        });
        const { done, value } = await Promise.race([reader.read(), timeout]).finally(() => {
          if (timer) clearTimeout(timer);
        });
        if (done) break;
        buffered += decoder.decode(value, { stream: true });
        if (buffered.length > 5_000_000) throw new Error("Agent NDJSON frame exceeded size limit");
        const lines = buffered.split(/\r?\n/);
        buffered = lines.pop() || "";
        for (const line of lines) {
          if (line.trim()) yield this.validateFrame(line);
        }
      }
      buffered += decoder.decode();
      if (buffered.trim()) yield this.validateFrame(buffered);
    } finally {
      controller.abort();
      try {
        reader.releaseLock();
      } catch {
        /* ignore */
      }
    }
  }

  private validateFrame(line: string): ExecutionFrame {
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      throw new Error("Agent returned invalid NDJSON");
    }
    if (!value || typeof value !== "object") throw new Error("Agent frame must be an object");
    const frame = value as Record<string, unknown>;
    if (typeof frame.type !== "string" || !FRAME_TYPES.has(frame.type)) {
      throw new Error(`Agent returned unsupported frame type: ${String(frame.type)}`);
    }
    if (!Number.isSafeInteger(frame.sequence) || Number(frame.sequence) < 0) {
      throw new Error("Agent frame sequence must be a non-negative integer");
    }
    return scrubObj(frame) as ExecutionFrame;
  }

  async health(): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.baseUrl}/health`, {
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) throw new Error(`Agent health failed (${response.status})`);
    return response.json() as Promise<Record<string, unknown>>;
  }

  /** Calls a protected route — catches AGENT_SHARED_KEY mismatch before research runs. */
  async ready(): Promise<Record<string, unknown>> {
    const key = this.agentKey();
    if (!key) throw new Error("agent_auth: AGENT_SHARED_KEY is not set on kiln-api");
    const response = await fetch(`${this.baseUrl}/ready`, {
      headers: this.headers(),
      signal: AbortSignal.timeout(10_000),
    });
    if (response.status === 401) {
      throw new Error(
        "agent_auth: invalid agent credentials — kiln-api AGENT_SHARED_KEY must match kiln-agent (delete API_TO_AGENT_KEY if set)",
      );
    }
    const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    if (!response.ok) throw new Error(`Agent ready failed (${response.status})`);
    return body;
  }

  async fetch(path: string, init: RequestInit = {}): Promise<Response> {
    return fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: { ...this.headers(Boolean(init.body)), ...(init.headers || {}) },
    });
  }
}
