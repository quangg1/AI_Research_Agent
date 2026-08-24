import { LlmCredentialVault } from "./llm-credential.vault";
import { sanitizeLlm, scrubObj, scrubText } from "./dto/llm-credential.dto";

describe("LlmCredentialVault", () => {
  test("holds a key in memory and drops it on release", () => {
    const vault = new LlmCredentialVault();
    vault.put("exec-1", { provider: "openai", apiKey: "sk-never-persist" });
    expect(vault.peek("exec-1")?.apiKey).toBe("sk-never-persist");
    vault.release("exec-1");
    expect(vault.peek("exec-1")).toBeUndefined();
  });

  test("expires keys after ttl", () => {
    const previous = process.env.LLM_CREDENTIAL_TTL_MS;
    process.env.LLM_CREDENTIAL_TTL_MS = "1";
    const vault = new LlmCredentialVault();
    vault.put("exec-2", { provider: "gemini", apiKey: "AIza-test-key" });
    const start = Date.now();
    while (Date.now() - start < 20) {
      /* wait past 1ms ttl */
    }
    expect(vault.peek("exec-2")).toBeUndefined();
    process.env.LLM_CREDENTIAL_TTL_MS = previous;
  });
});

describe("llm credential sanitization", () => {
  test("drops short visitor keys so they never leave the API process", () => {
    expect(sanitizeLlm({ provider: "gemini", apiKey: "short" })).toEqual({ provider: "gemini" });
    expect(sanitizeLlm({ provider: "openai", apiKey: "sk-user-secret-key" })?.apiKey).toBe(
      "sk-user-secret-key",
    );
  });

  test("scrubs key-shaped strings and apiKey fields", () => {
    expect(scrubText("failed Bearer sk-abcdefghijk extra")).toContain("***");
    expect(scrubObj({ llm: { apiKey: "sk-user-secret-key", provider: "openai" } })).toEqual({
      llm: { apiKey: "***", provider: "openai" },
    });
  });

  test("normalizes semicolon key pools and drops short fragments", () => {
    expect(
      sanitizeLlm({
        provider: "openai",
        apiKey: "sk-dead-key-aaaaaaa ; short ; sk-live-key-bbbbbbb",
      })?.apiKey,
    ).toBe("sk-dead-key-aaaaaaa;sk-live-key-bbbbbbb");
  });

  test("keeps extra visitor keys for failover and drops short ones", () => {
    expect(
      sanitizeLlm({
        provider: "gemini",
        keys: { openai: "sk-user-secret-key", grok: "short" },
      }),
    ).toEqual({ provider: "gemini", keys: { openai: "sk-user-secret-key" } });
  });
});
