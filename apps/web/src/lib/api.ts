import { z } from "zod";
import {
  CorpusStatsSchema,
  KnowledgeMatchSchema,
  KnowledgeStatsSchema,
  ResearchEnqueueResponseSchema,
  ResearchRunSchema,
  WorkspaceRunsSchema,
  parseApi,
  type CorpusStats,
  type KnowledgeMatch,
  type KnowledgeStats,
  type ResearchEnqueueResponse,
  type ResearchRun,
  type WorkspaceRuns,
} from "@kiln/contracts";

const API = import.meta.env.VITE_API_URL || "";

export class ApiClientError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown, fallback: string) {
    const message =
      typeof body === "object" && body && "message" in body
        ? String((body as { message: unknown }).message)
        : fallback;
    super(message || fallback);
    this.status = status;
    this.body = body;
  }
}

export async function api<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiClientError(res.status, data, res.statusText || "Request failed");
  }
  return parseApi(schema, data);
}

const ExamplesSchema = z.object({
  domain: z.string().optional(),
  queries: z.array(z.string()).optional(),
});
const TimelineSchema = z.object({
  id: z.string(),
  events: z.array(
    z.object({
      id: z.union([z.string(), z.number()]),
      event_type: z.string(),
      payload: z.unknown(),
      created_at: z.union([z.string(), z.date()]),
    }),
  ),
});

const PinSchema = z.object({ id: z.string(), pinned: z.boolean() });
const UnknownObjectSchema = z.record(z.unknown());

export const endpoints = {
  api: API,
  examples: () => api("/v1/examples", ExamplesSchema),
  status: () => api("/v1/status", UnknownObjectSchema),
  startResearch: (query: string, fresh = false) =>
    api("/v1/research", ResearchEnqueueResponseSchema, {
      method: "POST",
      body: JSON.stringify({ query, fresh }),
    }) as Promise<ResearchEnqueueResponse>,
  getRun: (id: string) => api(`/v1/research/${id}`, ResearchRunSchema) as Promise<ResearchRun>,
  resume: (id: string, body: unknown) =>
    api(`/v1/research/${id}/resume`, UnknownObjectSchema, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  eventsUrl: (id: string) => `${API}/v1/research/${id}/events`,
  workspace: () => api("/v1/workspace/runs", WorkspaceRunsSchema) as Promise<WorkspaceRuns>,
  timeline: (id: string) => api(`/v1/workspace/runs/${id}/timeline`, TimelineSchema),
  pin: (id: string, pinned = true) =>
    api(`/v1/workspace/runs/${id}/pin`, PinSchema, {
      method: "POST",
      body: JSON.stringify({ pinned }),
    }),
  corpus: () => api("/v1/corpus", CorpusStatsSchema) as Promise<CorpusStats>,
  refreshCorpus: () =>
    api("/v1/corpus/refresh", UnknownObjectSchema, { method: "POST" }),
  knowledge: () => api("/v1/knowledge", KnowledgeStatsSchema) as Promise<KnowledgeStats>,
  knowledgeMatch: (query: string) =>
    api(
      `/v1/knowledge/match?query=${encodeURIComponent(query)}`,
      KnowledgeMatchSchema,
    ) as Promise<KnowledgeMatch>,
  serving: (body: unknown) =>
    api("/v1/scenarios/serving", UnknownObjectSchema, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rag: (body: unknown) =>
    api("/v1/scenarios/rag", UnknownObjectSchema, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
