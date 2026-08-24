import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";
import katex from "katex";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import "katex/dist/katex.min.css";
import { slugifyHeading } from "../lib/memoToc";

export type MemoCite = { n?: number; url?: string; title?: string; quote?: string; tier?: string; host?: string };

/**
 * Pre-render math with KaTeX → HTML. Avoids remark-math `$` / `\(` pitfalls:
 * - `$…$` can swallow prose (spaces collapse)
 * - `\(` is CommonMark-escaped to `(` → shows as `((L_{user}))`
 */
export function prepMathMarkdown(text: string) {
  let out = (text || "").trim();

  out = out.replace(/\\mathcal\{L\}\{/g, "\\mathcal{L}_{");
  out = out.replace(/\\(lambda|theta|mu|alpha|beta)\{(\\text\{)/g, "\\$1_{$2");

  // Existing $$ … $$ display blocks
  out = replaceDisplayDollars(out);

  // Orphan: "as: $\n<eq>\nwhere … $"
  out = repairOrphanDollarEquations(out);

  // Bare equation lines
  out = out.replace(
    /^(?!<span)(?!\$\$)([A-Za-z\\][^\n]{0,240}?=[^\n]{0,240}?(?:\\times|\\frac|\\cdot|_\{)[^\n]{0,240})$/gm,
    (line) => {
      if (/\\times|\\frac|_\{/.test(line) && /=/.test(line) && !line.includes("katex")) {
        return katexBlock(line.trim());
      }
      return line;
    },
  );

  // Short inline $…$ → KaTeX HTML (skip currency-like $1,000$)
  out = replaceInlineDollars(out);

  // Bare Foo_{bar} still in prose
  out = wrapBareSubscripts(out);

  // Leftover $ (stray delimiters) → remove so they don't show
  out = out.replace(/(?<!\\)\$/g, "");

  // Any remaining \times in prose
  out = out.replace(/\\times/g, "×");

  return out;
}

function katexInline(tex: string): string {
  try {
    return katex.renderToString(tex.trim(), {
      throwOnError: false,
      displayMode: false,
      strict: "ignore",
    });
  } catch {
    return tex;
  }
}

function katexBlock(tex: string): string {
  try {
    const html = katex.renderToString(tex.trim(), {
      throwOnError: false,
      displayMode: true,
      strict: "ignore",
    });
    return `\n\n<div class="katex-block">${html}</div>\n\n`;
  } catch {
    return `\n\n<pre class="katex-fallback">${tex}</pre>\n\n`;
  }
}

function replaceDisplayDollars(text: string): string {
  return text.replace(/\$\$([\s\S]*?)\$\$/g, (_m, body: string) => katexBlock(body));
}

function repairOrphanDollarEquations(text: string): string {
  return text.replace(
    /\$\s*\n\s*([^\n]*?(?:\\times|\\frac|_\{)[^\n]*?=[^\n]+)\s*\n([\s\S]*?)\$/g,
    (_m, eq: string, rest: string) => {
      const prose = rest.trim();
      if (!prose) return katexBlock(eq);
      return `${katexBlock(eq)}${prose}`;
    },
  );
}

function replaceInlineDollars(text: string): string {
  // Don't touch inside HTML we already emitted
  const parts: string[] = [];
  let last = 0;
  const htmlRe = /<div class="katex-block">[\s\S]*?<\/div>|<span class="katex">[\s\S]*?<\/span>/g;
  let m: RegExpExecArray | null;
  while ((m = htmlRe.exec(text)) !== null) {
    parts.push(replaceInlineInSegment(text.slice(last, m.index)));
    parts.push(m[0]);
    last = m.index + m[0].length;
  }
  parts.push(replaceInlineInSegment(text.slice(last)));
  return parts.join("");
}

function replaceInlineInSegment(segment: string): string {
  return segment.replace(/\$([^\$\n]{1,100})\$/g, (_m, inner: string) => {
    const t = inner.trim();
    if (!t) return "";
    // Currency / plain numbers: $1,000$ or $8,000+$
    if (/^[\d,]+(?:\+)?(?:\.\d+)?$/.test(t)) return t;
    const words = t.match(/[A-Za-z]{3,}/g) || [];
    if (words.length >= 5 || /\bis\b.+\bis\b/i.test(t)) return t;
    if (/[_\\^]|\\times|\\frac|\\cdot|[A-Za-z]\s*=\s*|^\s*[A-Za-z0-9]+\s*$/.test(t) || t.length <= 40) {
      return katexInline(t);
    }
    return t;
  });
}

function wrapBareSubscripts(text: string): string {
  const parts: string[] = [];
  let last = 0;
  const htmlRe = /<div class="katex-block">[\s\S]*?<\/div>|<span class="katex">[\s\S]*?<\/span>/g;
  let m: RegExpExecArray | null;
  while ((m = htmlRe.exec(text)) !== null) {
    parts.push(wrapSubsRaw(text.slice(last, m.index)));
    parts.push(m[0]);
    last = m.index + m[0].length;
  }
  parts.push(wrapSubsRaw(text.slice(last)));
  return parts.join("");
}

function wrapSubsRaw(s: string): string {
  return s.replace(
    /\b([A-Za-z]{1,16}|\\[A-Za-z]+)_\{([A-Za-z0-9]+)\}/g,
    (_m, base: string, sub: string) => katexInline(`${base}_{${sub}}`),
  );
}

/** Wrap ASCII / box-flow diagrams so they scroll horizontally instead of wrapping/clipping. */
export function fenceAsciiDiagrams(text: string): string {
  const lines = (text || "").split("\n");
  const out: string[] = [];
  let buf: string[] = [];
  let inFence = false;

  const flush = () => {
    while (buf.length && !buf[buf.length - 1].trim()) buf.pop();
    if (!buf.length) return;
    out.push("```text", ...buf, "```");
    buf = [];
  };

  const isDiagramLine = (line: string) => {
    const t = line.trim();
    if (!t || /^```/.test(t)) return false;
    if (/^[▼▲►◄─│┌┐└┘├┤┬┴┼+|=\-]{3,}/.test(t)) return true;
    if ((t.match(/→|->|-->|—>/g) || []).length >= 1 && /\[[^\]]{2,}\]/.test(t)) return true;
    if ((t.match(/→|->|-->/g) || []).length >= 2) return true;
    // Bracket node on its own (flowchart), not bare citations like [12]
    if (/^\[[^\]]*[A-Za-z][^\]]*\]\s*$/.test(t) && !/^\[\d+(?:\s*,\s*\d+)*\]$/.test(t)) return true;
    return false;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^```/.test(line.trim())) {
      flush();
      inFence = !inFence;
      out.push(line);
      continue;
    }
    if (inFence) {
      out.push(line);
      continue;
    }
    if (isDiagramLine(line)) {
      buf.push(line);
      continue;
    }
    if (buf.length && !line.trim()) {
      const next = lines[i + 1];
      if (next != null && isDiagramLine(next)) {
        buf.push(line);
        continue;
      }
    }
    flush();
    out.push(line);
  }
  flush();
  return out.join("\n");
}

/** Typography + structure soft-fixes for memo display (not summarization). */
export function prepMemoMarkdown(text: string, opts?: { stripTitle?: string }) {
  let out = text || "";
  out = out.replace(
    /\[(?:DIRECT|INFERRED|DERIVED|RECOMMENDATION|SPECULATIVE)\]\s*/gi,
    "",
  );
  out = out.replace(
    /\b(?:DIRECT|INFERRED|DERIVED|RECOMMENDATION|SPECULATIVE)\s*:\s+/gi,
    "",
  );
  out = prepMathMarkdown(out);
  out = fenceAsciiDiagrams(out);
  // Soft hyphenation only outside fenced blocks — ZWSP after arrows wraps diagrams and clips them.
  out = out.replace(/(```[\s\S]*?```)|([^\n`]+)/g, (chunk, fence: string, prose: string) => {
    if (fence) return fence.replace(/\s*-->\s*/g, " → ").replace(/\s+->\s+/g, " → ");
    return prose
      .replace(/\s*-->\s*/g, " →\u200B ")
      .replace(/\s+->\s+/g, " →\u200B ")
      .replace(/ → /g, " →\u200B ");
  });
  out = out.replace(/^#{1,3}\s+([A-Z][A-Z0-9 /&:-]{8,80})\s*$/gm, (_m, title: string) => {
    const nice = title
      .toLowerCase()
      .replace(/\b([a-z])/g, (c: string) => c.toUpperCase())
      .replace(/\b(Llm|Rag|Api|Sota|Hitl)\b/g, (w: string) => w.toUpperCase());
    return `## ${nice}`;
  });
  if (opts?.stripTitle) {
    const esc = opts.stripTitle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    out = out.replace(new RegExp(`^#\\s+${esc}\\s*\\n+`, "i"), "");
  }
  return out;
}

const CITE_RE = /\[(\d+(?:\s*,\s*\d+)*)\]/g;

function citeMeta(n: number, cites?: MemoCite[]) {
  return cites?.find((c) => Number(c.n) === n);
}

function renderCites(
  text: string,
  cites?: MemoCite[],
  onCiteClick?: (n: number) => void,
): ReactNode[] {
  const parts: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  const re = new RegExp(CITE_RE.source, "g");
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const nums = m[1].split(/\s*,\s*/).map((x) => Number(x));
    parts.push(
      <span className="cite-group" key={`c-${m.index}-${m[1]}`}>
        {nums.map((n, i) => {
          const hit = citeMeta(n, cites);
          if (onCiteClick) {
            return (
              <button
                key={`${n}-${i}`}
                type="button"
                className="cite cite-btn"
                title={hit?.title || `Source [${n}]`}
                onClick={(e) => {
                  e.preventDefault();
                  onCiteClick(n);
                }}
              >
                [{n}]
              </button>
            );
          }
          return (
            <a
              key={`${n}-${i}`}
              className="cite"
              href={hit?.url || `#ref-${n}`}
              target={hit?.url ? "_blank" : undefined}
              rel="noreferrer"
              title={hit?.title || `Source [${n}]`}
            >
              [{n}]
            </a>
          );
        })}
      </span>,
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function withCites(
  node: ReactNode,
  cites?: MemoCite[],
  onCiteClick?: (n: number) => void,
): ReactNode {
  return Children.map(node, (child) => {
    if (typeof child === "string") return renderCites(child, cites, onCiteClick);
    if (isValidElement(child) && (child as ReactElement<{ children?: ReactNode }>).props.children != null) {
      const el = child as ReactElement<{ children?: ReactNode }>;
      return cloneElement(el, { children: withCites(el.props.children, cites, onCiteClick) });
    }
    return child;
  });
}

function headingId(children: ReactNode): string {
  const text = flattenText(children);
  return `memo-${slugifyHeading(text)}`;
}

function flattenText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flattenText).join("");
  if (isValidElement(node)) {
    return flattenText((node as ReactElement<{ children?: ReactNode }>).props.children);
  }
  return "";
}

export function MemoMarkdown({
  children,
  className = "md",
  citations,
  stripTitle,
  onCiteClick,
}: {
  children: string;
  className?: string;
  citations?: MemoCite[];
  stripTitle?: string;
  onCiteClick?: (n: number) => void;
}) {
  const source = prepMemoMarkdown(children, { stripTitle });
  const cite = (c: ReactNode) => withCites(c, citations, onCiteClick);
  return (
    <div className={className}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw]}
        components={{
          p: ({ children: c }) => <p>{cite(c)}</p>,
          li: ({ children: c }) => <li>{cite(c)}</li>,
          td: ({ children: c }) => <td>{cite(c)}</td>,
          th: ({ children: c }) => <th>{cite(c)}</th>,
          h1: ({ children: c }) => <h1>{cite(c)}</h1>,
          h2: ({ children: c }) => (
            <h2 id={headingId(c)}>{cite(c)}</h2>
          ),
          h3: ({ children: c }) => (
            <h3 id={headingId(c)}>{cite(c)}</h3>
          ),
          blockquote: ({ children: c }) => <blockquote className="memo-quote">{c}</blockquote>,
          table: ({ children: c }) => (
            <div className="memo-table-wrap">
              <table>{c}</table>
            </div>
          ),
          hr: () => <hr className="memo-rule" />,
          a: ({ href, children: c }) => (
            <a href={href} target={href?.startsWith("http") ? "_blank" : undefined} rel="noreferrer">
              {c}
            </a>
          ),
          div: ({ className: cn, children: c, ...rest }) => (
            <div className={cn} {...rest}>
              {c}
            </div>
          ),
        }}
      >
        {source}
      </Markdown>
    </div>
  );
}
