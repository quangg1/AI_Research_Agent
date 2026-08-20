import { Inject, Injectable, NotFoundException } from "@nestjs/common";
import { randomUUID } from "crypto";
import type { Pool, PoolClient, QueryResult } from "pg";
import { ExecuteJobPayload, ExecutionFrame, OutboxRecord } from "./research.types";

export const DATABASE_POOL = Symbol("DATABASE_POOL");

@Injectable()
export class ResearchRepository {
  constructor(@Inject(DATABASE_POOL) private readonly pool: Pool) {}

  query<T extends Record<string, unknown> = Record<string, unknown>>(
    text: string,
    values: unknown[] = [],
  ): Promise<QueryResult<T>> {
    return this.pool.query<T>(text, values);
  }

  async ping(): Promise<void> {
    await this.pool.query("SELECT 1");
  }

  private async transaction<T>(fn: (client: PoolClient) => Promise<T>): Promise<T> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const result = await fn(client);
      await client.query("COMMIT");
      return result;
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  async createRunWithOutbox(
    query: string,
    fresh: boolean,
    orgId: string,
    userId: string,
    title?: string,
  ) {
    const runId = randomUUID();
    const executionId = randomUUID();
    const payload: ExecuteJobPayload = {
      kind: "start",
      runId,
      executionId,
      executionVersion: 1,
      query,
      fresh,
    };
    return this.transaction(async (client) => {
      await client.query(
        `INSERT INTO research_runs
           (id, query, status, thread_id, execution_version, execution_id, org_id, created_by, title)
         VALUES ($1, $2, 'queued', $1, 1, $3, $4, $5, $6)`,
        [runId, query, executionId, orgId, userId, title || query.slice(0, 120)],
      );
      await client.query(
        `INSERT INTO run_events
           (run_id, execution_id, attempt, sequence, event_type, payload)
         VALUES ($1, $2, 0, 0, 'status:queued', $3::jsonb)`,
        [runId, executionId, JSON.stringify({ status: "queued" })],
      );
      await client.query(
        `INSERT INTO research_dispatch_outbox
           (run_id, execution_id, execution_version, job_name, payload)
         VALUES ($1, $2, 1, 'execute', $3::jsonb)`,
        [runId, executionId, JSON.stringify(payload)],
      );
      return { id: runId, status: "queued" as const, executionVersion: 1 };
    });
  }

  async getRunForOrg(runId: string, orgId: string) {
    const result = await this.pool.query(
      `SELECT * FROM research_runs
       WHERE id=$1 AND org_id=$2 AND deleted_at IS NULL`,
      [runId, orgId],
    );
    return result.rows[0] || null;
  }

  async softDeleteRun(runId: string, orgId: string) {
    const result = await this.pool.query(
      `UPDATE research_runs
       SET deleted_at=NOW(), archived=TRUE, updated_at=NOW()
       WHERE id=$1 AND org_id=$2 AND deleted_at IS NULL`,
      [runId, orgId],
    );
    return Boolean(result.rowCount);
  }

  async cancelRun(runId: string, orgId: string) {
    return this.transaction(async (client) => {
      const selected = await client.query<{
        status: string;
        execution_id: string | null;
        execution_version: number;
      }>(
        `SELECT status, execution_id, execution_version FROM research_runs
         WHERE id=$1 AND org_id=$2 AND deleted_at IS NULL FOR UPDATE`,
        [runId, orgId],
      );
      if (!selected.rowCount) return null;
      const row = selected.rows[0];
      if (["completed", "failed", "cancelled", "out_of_scope"].includes(row.status)) {
        return { id: runId, status: row.status };
      }
      await client.query(
        `UPDATE research_runs
         SET status='cancelled', completed_at=NOW(), updated_at=NOW(),
             current_job_id=NULL, error='Cancelled by user'
         WHERE id=$1`,
        [runId],
      );
      await client.query(
        `INSERT INTO run_events (run_id, execution_id, attempt, sequence, event_type, payload)
         VALUES ($1, $2, 0, 999998, 'status:cancelled', $3::jsonb)
         ON CONFLICT DO NOTHING`,
        [
          runId,
          row.execution_id,
          JSON.stringify({ status: "cancelled", reason: "user_cancel" }),
        ],
      );
      return { id: runId, status: "cancelled" as const };
    });
  }

  async notify(
    orgId: string,
    userId: string | null,
    runId: string | null,
    kind: string,
    title: string,
    body?: string,
  ) {
    await this.pool.query(
      `INSERT INTO notifications (id, org_id, user_id, run_id, kind, title, body)
       VALUES ($1, $2, $3, $4, $5, $6, $7)`,
      [randomUUID(), orgId, userId, runId, kind, title, body || null],
    );
  }

  async createResumeWithOutbox(
    runId: string,
    decision: Record<string, unknown>,
    orgId?: string,
  ) {
    return this.transaction(async (client) => {
      const selected = await client.query<{ status: string; execution_version: number }>(
        orgId
          ? `SELECT status, execution_version FROM research_runs
             WHERE id=$1 AND org_id=$2 AND deleted_at IS NULL FOR UPDATE`
          : `SELECT status, execution_version FROM research_runs WHERE id=$1 FOR UPDATE`,
        orgId ? [runId, orgId] : [runId],
      );
      if (!selected.rowCount) throw new NotFoundException("run not found");
      const status = selected.rows[0].status;
      if (status !== "awaiting_human" && status !== "awaiting_brief") {
        throw new NotFoundException(`run is not awaiting human input (status=${status})`);
      }
      const executionVersion = selected.rows[0].execution_version + 1;
      const executionId = randomUUID();
      const payload: ExecuteJobPayload = {
        kind: "resume",
        runId,
        executionId,
        executionVersion,
        decision,
      };
      await client.query(
        `UPDATE research_runs
         SET status='queued', execution_version=$2, execution_id=$3,
             current_job_id=NULL, completed_at=NULL, error=NULL, updated_at=NOW()
         WHERE id=$1`,
        [runId, executionVersion, executionId],
      );
      await client.query(
        `INSERT INTO run_events
           (run_id, execution_id, attempt, sequence, event_type, payload)
         VALUES ($1, $2, 0, 0, 'status:queued', $3::jsonb)`,
        [runId, executionId, JSON.stringify({ status: "queued", resumed: true })],
      );
      await client.query(
        `INSERT INTO research_dispatch_outbox
           (run_id, execution_id, execution_version, job_name, payload)
         VALUES ($1, $2, $3, 'execute', $4::jsonb)`,
        [runId, executionId, executionVersion, JSON.stringify(payload)],
      );
      return { id: runId, status: "queued" as const, executionVersion };
    });
  }

  async setCurrentJob(payload: ExecuteJobPayload, jobId: string): Promise<boolean> {
    const result = await this.pool.query(
      `UPDATE research_runs SET current_job_id=$4, status='running', updated_at=NOW(),
           started_at=COALESCE(started_at, NOW())
       WHERE id=$1 AND execution_version=$2 AND execution_id=$3
         AND status IN ('queued', 'running')`,
      [payload.runId, payload.executionVersion, payload.executionId, jobId],
    );
    return Boolean(result.rowCount);
  }

  async applyExecutionFrame(
    payload: ExecuteJobPayload,
    frame: ExecutionFrame,
    attempt: number,
  ): Promise<boolean> {
    return this.transaction(async (client) => {
      let status: string | null = "running";
      let terminal = false;
      if (frame.type === "heartbeat" || frame.type === "update") {
        status = "running";
      } else if (frame.type === "interrupt") {
        status = "awaiting_human";
      } else if (frame.type === "terminal") {
        status = frame.status || "completed";
        terminal = true;
      } else if (frame.type === "error") {
        status = null;
      }

      const locked = await client.query<{ status: string; org_id: string; created_by: string | null }>(
        `SELECT status, org_id, created_by FROM research_runs
         WHERE id=$1 AND execution_version=$2 AND execution_id=$3 FOR UPDATE`,
        [payload.runId, payload.executionVersion, payload.executionId],
      );
      if (!locked.rowCount) return false;
      if (locked.rows[0].status === "cancelled") return false;

      const snapshot = frame.snapshot || null;
      const metrics =
        frame.metrics ||
        (snapshot && typeof snapshot === "object"
          ? ((snapshot as { values?: { report?: { metrics?: Record<string, unknown> } } }).values
              ?.report?.metrics as Record<string, unknown> | undefined)
          : undefined);

      const result = await client.query(
        `UPDATE research_runs SET
           status=COALESCE($4, status),
           completed_at=CASE WHEN $5 THEN NOW() ELSE completed_at END,
           last_heartbeat_at=NOW(),
           result_json=CASE WHEN $6::jsonb IS NOT NULL THEN $6::jsonb ELSE result_json END,
           interrupt_payload=CASE
             WHEN $7::jsonb IS NOT NULL THEN $7::jsonb
             WHEN $5 THEN NULL
             ELSE interrupt_payload
           END,
           metrics_json=CASE WHEN $8::jsonb IS NOT NULL THEN $8::jsonb ELSE metrics_json END,
           error=COALESCE($9, error),
           updated_at=NOW()
         WHERE id=$1 AND execution_version=$2 AND execution_id=$3`,
        [
          payload.runId,
          payload.executionVersion,
          payload.executionId,
          status,
          terminal,
          snapshot ? JSON.stringify(snapshot) : null,
          frame.type === "interrupt"
            ? JSON.stringify((frame as { interrupt?: unknown; data?: unknown }).interrupt ?? (frame as { data?: unknown }).data ?? null)
            : null,
          metrics ? JSON.stringify(metrics) : null,
          frame.type === "error" ? String(frame.error || "execution error").slice(0, 4000) : null,
        ],
      );
      if (!result.rowCount) return false;

      await client.query(
        `INSERT INTO run_events
           (run_id, execution_id, attempt, sequence, event_type, payload)
         VALUES ($1, $2, $3, $4, $5, $6::jsonb)
         ON CONFLICT DO NOTHING`,
        [
          payload.runId,
          payload.executionId,
          attempt,
          frame.sequence,
          frame.type === "terminal" ? `status:${status}` : frame.type,
          JSON.stringify(frame),
        ],
      );

      const orgId = locked.rows[0].org_id;
      const createdBy = locked.rows[0].created_by;
      if (frame.type === "interrupt" && orgId) {
        await client.query(
          `INSERT INTO notifications (id, org_id, user_id, run_id, kind, title, body)
           VALUES ($1, $2, $3, $4, 'awaiting_human', $5, $6)`,
          [
            randomUUID(),
            orgId,
            createdBy,
            payload.runId,
            "Review needed",
            "A research run is waiting for your decision.",
          ],
        );
      }
      if (frame.type === "terminal" && orgId) {
        const kind = status === "completed" ? "completed" : "failed";
        await client.query(
          `INSERT INTO notifications (id, org_id, user_id, run_id, kind, title, body)
           VALUES ($1, $2, $3, $4, $5, $6, $7)`,
          [
            randomUUID(),
            orgId,
            createdBy,
            payload.runId,
            kind,
            status === "completed" ? "Research completed" : `Research ${status}`,
            `Run finished with status ${status}.`,
          ],
        );
      }
      return true;
    });
  }

  async failExecution(payload: ExecuteJobPayload, error: string): Promise<boolean> {
    const result = await this.pool.query(
      `UPDATE research_runs
       SET status='failed', error=$4, completed_at=NOW(), updated_at=NOW()
       WHERE id=$1 AND execution_version=$2 AND execution_id=$3
         AND status NOT IN ('completed','failed','out_of_scope','cancelled')`,
      [payload.runId, payload.executionVersion, payload.executionId, error.slice(0, 4000)],
    );
    if (result.rowCount) {
      await this.pool.query(
        `INSERT INTO run_events (run_id, execution_id, attempt, sequence, event_type, payload)
         VALUES ($1, $2, 0, 999999, 'status:failed', $3::jsonb)
         ON CONFLICT DO NOTHING`,
        [payload.runId, payload.executionId, JSON.stringify({ error })],
      );
    }
    return Boolean(result.rowCount);
  }

  async listEvents(runId: string, afterId: number, limit = 200) {
    return this.pool.query(
      `SELECT id, event_type, payload, execution_id, attempt, sequence, created_at
       FROM run_events WHERE run_id=$1 AND id>$2 ORDER BY id ASC LIMIT $3`,
      [runId, afterId, limit],
    );
  }

  async dispatchNext(publish: (record: OutboxRecord) => Promise<void>): Promise<boolean> {
    return this.transaction(async (client) => {
      const selected = await client.query(
        `SELECT id, run_id, execution_id, execution_version, job_name, payload, attempts
         FROM research_dispatch_outbox
         WHERE dispatched_at IS NULL
         ORDER BY created_at, id
         FOR UPDATE SKIP LOCKED LIMIT 1`,
      );
      if (!selected.rowCount) return false;
      const row = selected.rows[0];
      const payload =
        typeof row.payload === "string" ? JSON.parse(row.payload) : (row.payload as ExecuteJobPayload);
      const record: OutboxRecord = {
        id: row.id,
        run_id: row.run_id,
        execution_id: row.execution_id,
        execution_version: row.execution_version,
        job_name: "execute",
        payload,
        attempts: row.attempts,
      };
      await client.query(
        `UPDATE research_dispatch_outbox SET attempts=attempts+1 WHERE id=$1`,
        [record.id],
      );
      await publish(record);
      await client.query(
        `UPDATE research_dispatch_outbox SET dispatched_at=NOW() WHERE id=$1`,
        [record.id],
      );
      return true;
    });
  }
}
