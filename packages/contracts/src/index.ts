import { z } from "zod";

export const RunStatusSchema = z.enum([
  "queued",
  "running",
  "awaiting_human",
  "completed",
  "failed",
  "out_of_scope",
  "cancelled",
]);
export type RunStatus = z.infer<typeof RunStatusSchema>;

export const ApiErrorSchema = z.object({
  statusCode: z.number().optional(),
  message: z.union([z.string(), z.array(z.string())]).optional(),
  error: z.string().optional(),
});
export type ApiError = z.infer<typeof ApiErrorSchema>;

export const StartResearchRequestSchema = z.object({
  query: z.string().min(8).max(4000),
  fresh: z.boolean().optional(),
});
export type StartResearchRequest = z.infer<typeof StartResearchRequestSchema>;

export const ResumeResearchRequestSchema = z.object({
  action: z.string().optional(),
  notes: z.string().optional(),
  extra_questions: z.array(z.string()).optional(),
  brief: z.record(z.unknown()).optional(),
});
export type ResumeResearchRequest = z.infer<typeof ResumeResearchRequestSchema>;

export const ResearchEnqueueResponseSchema = z.object({
  id: z.string().uuid(),
  status: RunStatusSchema,
});
export type ResearchEnqueueResponse = z.infer<typeof ResearchEnqueueResponseSchema>;

export const InterruptPayloadSchema = z
  .object({
    type: z.string().optional(),
    gate: z.string().optional(),
    title: z.string().optional(),
    summary: z.string().optional(),
    questions: z.array(z.string()).optional(),
    brief: z.record(z.unknown()).optional(),
    report_preview: z.unknown().optional(),
  })
  .passthrough();
export type InterruptPayload = z.infer<typeof InterruptPayloadSchema>;

export const AgentSnapshotSchema = z
  .object({
    thread_id: z.string().optional(),
    status: z.string().optional(),
    query: z.string().optional(),
    current_node: z.string().optional(),
    progress: z.number().optional(),
    hint: z.string().optional(),
    eta_s: z.number().optional(),
    elapsed_s: z.number().optional(),
    interrupt: InterruptPayloadSchema.nullable().optional(),
    values: z.any().optional(),
    error: z.string().nullable().optional(),
  })
  .passthrough();
export type AgentSnapshot = z.infer<typeof AgentSnapshotSchema>;

export const ResearchRunSchema = z
  .object({
    id: z.string(),
    query: z.string(),
    status: RunStatusSchema.or(z.string()),
    thread_id: z.string().optional(),
    result_json: z.unknown().optional(),
    interrupt_payload: z.unknown().optional(),
    metrics_json: z.unknown().optional(),
    error: z.string().nullable().optional(),
    pinned: z.boolean().optional(),
    created_at: z.union([z.string(), z.date()]).optional(),
    updated_at: z.union([z.string(), z.date()]).optional(),
    agent: AgentSnapshotSchema.optional(),
  })
  .passthrough();
export type ResearchRun = z.infer<typeof ResearchRunSchema>;

export const TimelineEventSchema = z.object({
  id: z.union([z.string(), z.number()]),
  event_type: z.string(),
  payload: z.unknown(),
  created_at: z.union([z.string(), z.date()]),
});
export type TimelineEvent = z.infer<typeof TimelineEventSchema>;

export const CorpusItemSchema = z.object({
  id: z.string().optional(),
  title: z.string().nullable().optional(),
  url: z.string().nullable().optional(),
  host: z.string().nullable().optional(),
  tier: z.string().nullable().optional(),
  snippet: z.string().nullable().optional(),
  credibility: z.number().optional(),
  published: z.string().nullable().optional(),
});
export type CorpusItem = z.infer<typeof CorpusItemSchema>;

export const CorpusStatsSchema = z.object({
  documents: z.number(),
  hosts: z.record(z.number()).optional(),
  tiers: z.record(z.number()).optional(),
  items: z.array(CorpusItemSchema).optional(),
  generation: z.number().optional(),
});
export type CorpusStats = z.infer<typeof CorpusStatsSchema>;

export const KnowledgeStatsSchema = z.object({
  records: z.number(),
  path: z.string().optional(),
  backend: z.string().optional(),
  total_reuse: z.number().optional(),
  avg_depth: z.number().optional(),
  items: z.array(z.record(z.unknown())).optional(),
});
export type KnowledgeStats = z.infer<typeof KnowledgeStatsSchema>;

export const KnowledgeMatchSchema = z.object({
  match: z.boolean(),
  mode: z.string().optional(),
  similarity: z.number().optional(),
  age_days: z.number().optional(),
  id: z.string().optional(),
  goal: z.string().optional(),
  version: z.number().optional(),
  sources: z.number().optional(),
});
export type KnowledgeMatch = z.infer<typeof KnowledgeMatchSchema>;

export const WorkspaceRunsSchema = z.object({
  runs: z.array(
    z.object({
      id: z.string(),
      query: z.string(),
      title: z.string().nullable().optional(),
      status: z.string(),
      pinned: z.boolean().optional(),
      archived: z.boolean().optional(),
      created_at: z.union([z.string(), z.date()]).optional(),
      updated_at: z.union([z.string(), z.date()]).optional(),
    }),
  ),
  next_cursor: z.string().nullable().optional(),
});
export type WorkspaceRuns = z.infer<typeof WorkspaceRunsSchema>;

export const ExecutionFrameSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("update"),
    runId: z.string(),
    executionId: z.string(),
    snapshot: AgentSnapshotSchema,
    current_node: z.string().optional(),
  }),
  z.object({
    type: z.literal("heartbeat"),
    runId: z.string(),
    executionId: z.string(),
    snapshot: AgentSnapshotSchema.optional(),
  }),
  z.object({
    type: z.literal("interrupt"),
    runId: z.string(),
    executionId: z.string(),
    interrupt: InterruptPayloadSchema,
    snapshot: AgentSnapshotSchema,
  }),
  z.object({
    type: z.literal("terminal"),
    runId: z.string(),
    executionId: z.string(),
    status: z.enum(["completed", "cancelled", "out_of_scope", "failed"]),
    snapshot: AgentSnapshotSchema,
  }),
  z.object({
    type: z.literal("error"),
    runId: z.string(),
    executionId: z.string(),
    error: z.string(),
    retryable: z.boolean().default(true),
  }),
]);
export type ExecutionFrame = z.infer<typeof ExecutionFrameSchema>;

export function parseApi<T>(schema: z.ZodType<T>, data: unknown): T {
  return schema.parse(data);
}
