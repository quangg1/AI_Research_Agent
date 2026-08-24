import { Injectable } from "@nestjs/common";
import type { LlmCredentialDto } from "./dto/llm-credential.dto";

type Entry = { cred: LlmCredentialDto; expires: number };

/** Process-memory only. Never written to Postgres, outbox JSON, or BullMQ payloads. */
@Injectable()
export class LlmCredentialVault {
  private readonly store = new Map<string, Entry>();
  private readonly ttlMs = Number(process.env.LLM_CREDENTIAL_TTL_MS || 4 * 60 * 60 * 1000);

  put(executionId: string, cred: LlmCredentialDto) {
    this.gc();
    this.store.set(executionId, { cred, expires: Date.now() + this.ttlMs });
  }

  peek(executionId: string): LlmCredentialDto | undefined {
    const entry = this.store.get(executionId);
    if (!entry) return undefined;
    if (entry.expires <= Date.now()) {
      this.store.delete(executionId);
      return undefined;
    }
    return entry.cred;
  }

  release(executionId: string) {
    this.store.delete(executionId);
  }

  private gc() {
    const now = Date.now();
    for (const [id, entry] of this.store) {
      if (entry.expires <= now) this.store.delete(id);
    }
  }
}
