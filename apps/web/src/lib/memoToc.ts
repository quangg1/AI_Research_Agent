export type MemoHeading = { id: string; level: 2 | 3; text: string };

/** Extract ## / ### headings for TOC (after prep; ignores # title). */
export function extractMemoToc(markdown: string): MemoHeading[] {
  const out: MemoHeading[] = [];
  const seen = new Map<string, number>();
  for (const line of (markdown || "").split("\n")) {
    const m = /^(#{2,3})\s+(.+?)\s*$/.exec(line);
    if (!m) continue;
    const level = m[1].length as 2 | 3;
    const text = m[2].replace(/\[\d+(?:\s+(?:peer|primary|repo|specialist|vendor|news|industry))?\]/gi, "").trim();
    if (!text || text.length > 120) continue;
    let slug = text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 64) || "section";
    const n = (seen.get(slug) || 0) + 1;
    seen.set(slug, n);
    if (n > 1) slug = `${slug}-${n}`;
    out.push({ id: `memo-${slug}`, level, text });
  }
  return out;
}

export function slugifyHeading(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/\[(\d+)\]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 64) || "section"
  );
}
