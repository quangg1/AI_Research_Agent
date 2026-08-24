import { Injectable } from "@nestjs/common";
import { ExecuteJobPayload, ExecutionFrame } from "./research.types";
import { agentLlmPayload, scrubObj, scrubText, type LlmCredentialDto } from "./dto/llm-credential.dto";

const FRAME_TYPES = new Set(["update", "heartbeat", "interrupt", "terminal", "error"]);

@Injectable()
export class AgentExecutionClient {
  private readonly baseUrl = process.env.AGENT_BASE_URL || "http://localhost:8000";
  private readonly idleTimeoutMs = Number(process.env.AGENT_STREAM_IDLE_TIMEOUT_MS || 120_000);

  private headers(contentType = false): Record<string, string> {
    const key = process.env.API_TO_AGENT_KEY || process.env.AGENT_SHARED_KEY;
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
            ...(llm ? { llm: agentLlmPayload(llm) } : {}),
          }
        : {
            kind: "resume",
            runId: payload.runId,
            executionId: payload.executionId,
            decision: payload.decision || {},
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
      headers: this.headers(),
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) throw new Error(`Agent health failed (${response.status})`);
    return response.json() as Promise<Record<string, unknown>>;
  }

  async fetch(path: string, init: RequestInit = {}): Promise<Response> {
    return fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: { ...this.headers(Boolean(init.body)), ...(init.headers || {}) },
    });
  }
}
