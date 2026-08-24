import { Type } from "class-transformer";
import { IsIn, IsOptional, IsString, Length, ValidateIf, ValidateNested } from "class-validator";

export const LLM_PROVIDERS = ["gemini", "openai", "grok"] as const;
export type LlmProvider = (typeof LLM_PROVIDERS)[number];

export class LlmKeysDto {
  @IsOptional()
  @ValidateIf((_, value) => typeof value === "string" && value.length > 0)
  @IsString()
  @Length(8, 4096)
  gemini?: string;

  @IsOptional()
  @ValidateIf((_, value) => typeof value === "string" && value.length > 0)
  @IsString()
  @Length(8, 4096)
  openai?: string;

  @IsOptional()
  @ValidateIf((_, value) => typeof value === "string" && value.length > 0)
  @IsString()
  @Length(8, 4096)
  grok?: string;
}

export class LlmCredentialDto {
  @IsIn(LLM_PROVIDERS)
  provider!: LlmProvider;

  @IsOptional()
  @ValidateIf((_, value) => typeof value === "string" && value.length > 0)
  @IsString()
  @Length(8, 4096)
  apiKey?: string;

  @IsOptional()
  @IsString()
  @Length(1, 200)
  model?: string;

  @IsOptional()
  @ValidateNested()
  @Type(() => LlmKeysDto)
  keys?: LlmKeysDto;
}

export function llmByokRequired(): boolean {
  const raw = (process.env.LLM_BYOK_REQUIRED || "").toLowerCase();
  if (raw === "true" || raw === "1") return true;
  if (raw === "false" || raw === "0") return false;
  return false;
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

function cleanKey(raw?: string) {
  const parts = splitApiKeys(raw);
  return parts.length ? parts.join(";") : undefined;
}

export function sanitizeKeys(keys?: LlmKeysDto | null): LlmKeysDto | undefined {
  if (!keys) return undefined;
  const cleaned: LlmKeysDto = {
    ...(cleanKey(keys.gemini) ? { gemini: cleanKey(keys.gemini) } : {}),
    ...(cleanKey(keys.openai) ? { openai: cleanKey(keys.openai) } : {}),
    ...(cleanKey(keys.grok) ? { grok: cleanKey(keys.grok) } : {}),
  };
  return Object.keys(cleaned).length ? cleaned : undefined;
}

export function hasVisitorSecrets(cred?: LlmCredentialDto | null) {
  if (!cred) return false;
  if (cleanKey(cred.apiKey)) return true;
  const keys = sanitizeKeys(cred.keys);
  return Boolean(keys && Object.keys(keys).length);
}

export function sanitizeLlm(dto?: LlmCredentialDto | null): LlmCredentialDto | undefined {
  if (!dto?.provider) return undefined;
  const apiKey = cleanKey(dto.apiKey);
  const keys = sanitizeKeys(dto.keys);
  return {
    provider: dto.provider,
    ...(apiKey ? { apiKey } : {}),
    model: dto.model?.trim() || undefined,
    ...(keys ? { keys } : {}),
  };
}

const KEY_SHAPE = /(sk-[A-Za-z0-9_-]{10,}|AIza[A-Za-z0-9_-]{10,}|xai-[A-Za-z0-9_-]{10,}|hf_[A-Za-z0-9_-]{10,}|Bearer\s+\S+)/gi;
const SECRET_KEYS = new Set(["apikey", "api_key", "authorization", "x-api-key", "secret", "password", "token"]);

export function scrubText(text: string, extra: string[] = []): string {
  if (!text) return text;
  let out = text;
  for (const secret of extra) {
    for (const part of splitApiKeys(secret).concat(secret && secret.length >= 8 ? [secret] : [])) {
      if (part) out = out.split(part).join("***");
    }
  }
  return out.replace(KEY_SHAPE, "***");
}

export function scrubObj(value: unknown, extra: string[] = []): unknown {
  if (typeof value === "string") return scrubText(value, extra);
  if (Array.isArray(value)) return value.map((item) => scrubObj(item, extra));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
      const name = key.toLowerCase().replace(/-/g, "_");
      if (SECRET_KEYS.has(name) || name.includes("api_key") || name.endsWith("apikey")) {
        out[key] = "***";
      } else {
        out[key] = scrubObj(nested, extra);
      }
    }
    return out;
  }
  return value;
}

export function agentLlmPayload(cred?: LlmCredentialDto): LlmCredentialDto | undefined {
  if (!cred?.provider) return undefined;
  const keys = sanitizeKeys(cred.keys);
  return {
    provider: cred.provider,
    ...(cred.apiKey ? { apiKey: cred.apiKey } : {}),
    ...(cred.model ? { model: cred.model } : {}),
    ...(keys ? { keys } : {}),
  };
}
