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
const AUTH_MODE = (import.meta.env.VITE_AUTH_MODE || "dev").toLowerCase();

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

type TokenProvider = () => Promise<string | null>;
let tokenProvider: TokenProvider = async () => null;

export function setTokenProvider(provider: TokenProvider) {
  tokenProvider = provider;
}

async function authHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {};
  if (AUTH_MODE === "dev" || AUTH_MODE === "disabled") {
    headers["X-Dev-User-Id"] = localStorage.getItem("kiln_dev_user") || "user_dev";
    headers["X-Dev-Org-Id"] = localStorage.getItem("kiln_dev_org") || "org_default";
    headers["X-Dev-Role"] = localStorage.getItem("kiln_dev_role") || "org:admin";
    return headers;
  }
  const token = await tokenProvider();
  if (token) headers.Authorization = `Bearer ${token}`;
  const orgId = localStorage.getItem("kiln_org_id");
  if (orgId) headers["X-Org-Id"] = orgId;
  return headers;
}

export async function getAuthHeaders() {
  return authHeaders();
}

export async function api<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(await authHeaders()),
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
      summary: z.string().optional(),
    }),
  ),
});

const PinSchema = z.object({ id: z.string(), pinned: z.boolean() });
const UnknownObjectSchema = z.record(z.unknown());
const WorkspaceListSchema = WorkspaceRunsSchema.extend({
  next_cursor: z.string().nullable().optional(),
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
});

export const endpoints = {
  api: API,
  authMode: AUTH_MODE,
  examples: () => api("/v1/examples", ExamplesSchema),
  status: () => api("/v1/status", UnknownObjectSchema),
  health: () => api("/health", UnknownObjectSchema),
  me: () => api("/v1/me", UnknownObjectSchema),
  startResearch: (query: string, fresh = false, llm?: { provider: string; apiKey?: string; model?: string }) =>
    api("/v1/research", ResearchEnqueueResponseSchema, {
      method: "POST",
      body: JSON.stringify({ query, fresh, ...(llm ? { llm } : {}) }),
    }) as Promise<ResearchEnqueueResponse>,
  getRun: (id: string) => api(`/v1/research/${id}`, ResearchRunSchema) as Promise<ResearchRun>,
  getGraph: (id: string) => api(`/v1/research/${id}/graph`, UnknownObjectSchema),
  resume: (id: string, body: unknown) =>
    api(`/v1/research/${id}/resume`, UnknownObjectSchema, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancel: (id: string) =>
    api(`/v1/research/${id}/cancel`, UnknownObjectSchema, { method: "POST" }),
  duplicate: (id: string, llm?: { provider: string; apiKey?: string; model?: string }) =>
    api(`/v1/research/${id}/duplicate`, ResearchEnqueueResponseSchema, {
      method: "POST",
      body: JSON.stringify(llm ? { llm } : {}),
    }),
  deleteRun: (id: string) =>
    api(`/v1/research/${id}`, UnknownObjectSchema, { method: "DELETE" }),
  eventsUrl: async (id: string) => {
    const headers = await authHeaders();
    const qs = new URLSearchParams();
    // EventSource cannot set headers; for Clerk mode the nginx/proxy must use cookies
    // or we poll. Dev mode passes via query only for EventSource fallback is avoided —
    // we use fetch stream when possible. Keep relative URL for same-origin proxy.
    void headers;
    void qs;
    return `${API}/v1/research/${id}/events`;
  },
  workspace: (params: Record<string, string | undefined> = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v) q.set(k, v);
    });
    const suffix = q.toString() ? `?${q}` : "";
    return api(`/v1/workspace/runs${suffix}`, WorkspaceListSchema);
  },
  timeline: (id: string) => api(`/v1/workspace/runs/${id}/timeline`, TimelineSchema),
  pin: (id: string, pinned = true) =>
    api(`/v1/workspace/runs/${id}/pin`, PinSchema, {
      method: "POST",
      body: JSON.stringify({ pinned }),
    }),
  patchRun: (id: string, body: { title?: string; archived?: boolean; pinned?: boolean }) =>
    api(`/v1/workspace/runs/${id}`, UnknownObjectSchema, {
      method: "PATCH",
      body: JSON.stringify(body),
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
  createShare: (id: string, expires_in_days = 14) =>
    api(`/v1/research/${id}/share`, UnknownObjectSchema, {
      method: "POST",
      body: JSON.stringify({ expires_in_days }),
    }),
  getShared: (token: string) => api(`/v1/share/${token}`, ResearchRunSchema),
  billing: () => api("/v1/billing", UnknownObjectSchema),
  checkout: (body: { success_url?: string; cancel_url?: string } = {}) =>
    api("/v1/billing/checkout", UnknownObjectSchema, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  portal: (body: { return_url?: string } = {}) =>
    api("/v1/billing/portal", UnknownObjectSchema, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  notifications: () =>
    api(
      "/v1/notifications",
      z.object({
        notifications: z.array(
          z.object({
            id: z.string(),
            run_id: z.string().nullable().optional(),
            kind: z.string(),
            title: z.string(),
            body: z.string().nullable().optional(),
            read_at: z.union([z.string(), z.date()]).nullable().optional(),
            created_at: z.union([z.string(), z.date()]),
          }),
        ),
      }),
    ),
  readNotification: (id: string) =>
    api(`/v1/notifications/${id}/read`, UnknownObjectSchema, { method: "POST" }),
  listApiKeys: async () => {
    const res = await fetch(`${API}/v1/org/api-keys`, {
      headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    });
    const data = await res.json().catch(() => []);
    if (!res.ok) throw new ApiClientError(res.status, data, "Failed to list API keys");
    return Array.isArray(data) ? data : [];
  },
  createApiKey: (name: string) =>
    api("/v1/org/api-keys", UnknownObjectSchema, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  revokeApiKey: (id: string) =>
    api(`/v1/org/api-keys/${id}`, UnknownObjectSchema, { method: "DELETE" }),
  exportOrg: () => api("/v1/org/export", UnknownObjectSchema),
  track: (event_name: string, props: Record<string, unknown> = {}) =>
    api("/v1/events", UnknownObjectSchema, {
      method: "POST",
      body: JSON.stringify({ event_name, props }),
    }),
};

/** EventSource with auth: for same-origin + cookie sessions. Dev uses poll-only fallback headers via fetch polyfill. */
export async function openRunEvents(runId: string, onMessage: () => void): Promise<() => void> {
  if (AUTH_MODE === "dev" || AUTH_MODE === "disabled") {
    // EventSource cannot set custom headers; poll instead when not same-origin cookie auth.
    let stop = false;
    const tick = async () => {
      while (!stop) {
        onMessage();
        await new Promise((r) => setTimeout(r, 1500));
      }
    };
    void tick();
    return () => {
      stop = true;
    };
  }
  const url = `${API}/v1/research/${runId}/events`;
  const es = new EventSource(url, { withCredentials: true } as EventSourceInit);
  es.onmessage = () => onMessage();
  return () => es.close();
}
