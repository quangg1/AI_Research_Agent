export const LLM_PROVIDERS = ["gemini", "openai", "grok"] as const;
export type LlmProvider = (typeof LLM_PROVIDERS)[number];

export type ByokState = {
  provider: LlmProvider;
  apiKey: string;
  keys: Record<LlmProvider, string>;
  model: string;
  rememberSession: boolean;
  acknowledged: boolean;
};

export const PROVIDER_META: Record<
  LlmProvider,
  { label: string; hint: string; docs: string; placeholder: string; defaultModel: string }
> = {
  gemini: {
    label: "Google Gemini",
    hint: "Tried first if selected. Separate multiple keys for this provider with ; — a dead or empty-credit key fails over to the next.",
    docs: "https://aistudio.google.com/apikey",
    placeholder: "AIza…;AIza…",
    defaultModel: "gemini-3.6-flash",
  },
  openai: {
    label: "OpenAI",
    hint: "Tried first if selected. Separate multiple keys for this provider with ; — a dead or empty-credit key fails over to the next.",
    docs: "https://platform.openai.com/api-keys",
    placeholder: "sk-…;sk-…",
    defaultModel: "gpt-4.1-mini",
  },
  grok: {
    label: "xAI Grok",
    hint: "Tried first if selected. Separate multiple keys for this provider with ; — a dead or empty-credit key fails over to the next.",
    docs: "https://console.x.ai/",
    placeholder: "xai-…;xai-…",
    defaultModel: "grok-4.6",
  },
};

const STORAGE_KEY = "kiln_provider_session";

export const EMPTY_BYOK: ByokState = {
  provider: "gemini",
  apiKey: "",
  keys: { gemini: "", openai: "", grok: "" },
  model: "",
  rememberSession: true,
  acknowledged: false,
};

function normalizeKeys(raw?: Partial<Record<LlmProvider, string>>): Record<LlmProvider, string> {
  return {
    gemini: typeof raw?.gemini === "string" ? raw.gemini : "",
    openai: typeof raw?.openai === "string" ? raw.openai : "",
    grok: typeof raw?.grok === "string" ? raw.grok : "",
  };
}

export function splitApiKeys(raw?: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of (raw || "").split(";")) {
    const key = part.trim();
    if (key.length >= 8 && !seen.has(key)) {
      seen.add(key);
      out.push(key);
    }
  }
  return out;
}

export function visitorKeys(state: ByokState): Record<LlmProvider, string> {
  const keys = normalizeKeys(state.keys);
  const preferred = state.apiKey.trim();
  if (splitApiKeys(preferred).length) keys[state.provider] = preferred;
  return keys;
}

export function visitorKeyList(state: ByokState) {
  return LLM_PROVIDERS.filter((id) => splitApiKeys(visitorKeys(state)[id]).length > 0);
}

export function loadByok(): ByokState {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...EMPTY_BYOK, keys: { ...EMPTY_BYOK.keys } };
    const parsed = JSON.parse(raw) as Partial<ByokState>;
    const provider = LLM_PROVIDERS.includes(parsed.provider as LlmProvider)
      ? (parsed.provider as LlmProvider)
      : "gemini";
    return {
      ...EMPTY_BYOK,
      keys: { ...EMPTY_BYOK.keys },
      provider,
      model: typeof parsed.model === "string" ? parsed.model : "",
      rememberSession: parsed.rememberSession !== false,
    };
  } catch {
    return { ...EMPTY_BYOK, keys: { ...EMPTY_BYOK.keys } };
  }
}

export function persistByok(state: ByokState) {
  if (!state.rememberSession) {
    sessionStorage.removeItem(STORAGE_KEY);
    return;
  }
  sessionStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      provider: state.provider,
      model: state.model,
      rememberSession: true,
    }),
  );
}

export function clearByok() {
  sessionStorage.removeItem(STORAGE_KEY);
}

export function llmPayload(state: ByokState) {
  // Never send visitor keys until the billing checkbox is checked.
  if (!state.acknowledged) {
    return {
      provider: state.provider,
      ...(state.model.trim() ? { model: state.model.trim() } : {}),
    };
  }
  const keys = visitorKeys(state);
  const filled = Object.fromEntries(
    LLM_PROVIDERS.filter((id) => splitApiKeys(keys[id]).length > 0).map((id) => [
      id,
      splitApiKeys(keys[id]).join(";"),
    ]),
  );
  const preferred = splitApiKeys(keys[state.provider]).join(";");
  return {
    provider: state.provider,
    ...(preferred ? { apiKey: preferred } : {}),
    model: state.model.trim() || undefined,
    ...(Object.keys(filled).length ? { keys: filled } : {}),
  };
}

export function needsVisitorKey(state: ByokState, creditsForced: boolean) {
  return creditsForced || visitorKeyList(state).length > 0;
}

export function byokReady(state: ByokState, creditsForced: boolean) {
  const pasted = visitorKeyList(state);
  // Hosted path stays usable even if the user typed a key but has not
  // acknowledged yet (keys are only sent after ack — see llmPayload).
  if (!creditsForced) return true;
  return state.acknowledged && pasted.length > 0;
}

export function isCreditsExhaustedMessage(message: string) {
  const blob = (message || "").toLowerCase();
  return (
    blob.includes("credits are exhausted") ||
    blob.includes("out of credits") ||
    blob.includes("all configured providers")
  );
}
