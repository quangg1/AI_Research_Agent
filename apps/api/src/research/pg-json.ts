/** Sanitize values before JSON.stringify + PostgreSQL ::jsonb cast. */
export function sanitizeForPgJson(value: unknown): unknown {
  if (typeof value === "string") {
    return value.replace(/\u0000/g, " ").replace(/[\uD800-\uDFFF]/g, "");
  }
  if (Array.isArray(value)) {
    return value.map(sanitizeForPgJson);
  }
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
      out[key] = sanitizeForPgJson(entry);
    }
    return out;
  }
  return value;
}

export function stringifyPgJsonb(value: unknown): string {
  return JSON.stringify(sanitizeForPgJson(value));
}
