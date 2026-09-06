import { sanitizeForPgJson, stringifyPgJsonb } from "./pg-json";

describe("pg-json", () => {
  test("strips null bytes that PostgreSQL jsonb rejects", () => {
    const raw = { values: { evidence: [{ full_text: "accuracy\x0091%" }] } };
    const json = stringifyPgJsonb(raw);
    expect(json).not.toContain("\\u0000");
    expect(json).toContain("accuracy");
    expect(JSON.parse(json).values.evidence[0].full_text).toBe("accuracy 91%");
  });

  test("removes lone surrogates", () => {
    const lone = "\uD800";
    const cleaned = sanitizeForPgJson({ text: `before${lone}after` }) as { text: string };
    expect(cleaned.text).toBe("beforeafter");
  });
});
