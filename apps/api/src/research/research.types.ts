export const TERMINAL_STATUSES = ["completed", "failed", "out_of_scope", "cancelled"] as const;
export type TerminalStatus = (typeof TERMINAL_STATUSES)[number];
export type ResearchStatus =
  | "queued"
  | "running"
  | "awaiting_human"
  | "awaiting_brief"
  | TerminalStatus;

export type ExecutionFrameType =
  | "update"
  | "heartbeat"
  | "interrupt"
  | "terminal"
  | "error";

export interface ExecutionFrame {
  type: ExecutionFrameType;
  sequence: number;
  runId?: string;
  executionId?: string;
  snapshot?: Record<string, unknown>;
  data?: unknown;
  interrupt?: unknown;
  status?: ResearchStatus;
  error?: string;
  retryable?: boolean;
  current_node?: string;
  metrics?: Record<string, unknown>;
}

export interface ExecuteJobPayload {
  kind: "start" | "resume";
  runId: string;
  executionId: string;
  executionVersion: number;
  query?: string;
  fresh?: boolean;
  decision?: Record<string, unknown>;
}

export interface OutboxRecord {
  id: string | number;
  run_id: string;
  execution_id: string;
  execution_version: number;
  job_name: "execute";
  payload: ExecuteJobPayload;
  attempts: number;
}
