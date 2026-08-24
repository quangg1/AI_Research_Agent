import { extractMemoToc, type MemoHeading } from "../lib/memoToc";

export type ReaderPrefs = {
  readingMode: boolean;
  fontScale: number;
  measure: "narrow" | "default" | "wide";
};

const PREFS_KEY = "kiln_reader_prefs";

export function loadReaderPrefs(): ReaderPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return { readingMode: false, fontScale: 1, measure: "default" };
    const p = JSON.parse(raw);
    return {
      readingMode: !!p.readingMode,
      fontScale: Math.min(1.35, Math.max(0.9, Number(p.fontScale) || 1)),
      measure: p.measure === "narrow" || p.measure === "wide" ? p.measure : "default",
    };
  } catch {
    return { readingMode: false, fontScale: 1, measure: "default" };
  }
}

export function saveReaderPrefs(p: ReaderPrefs) {
  localStorage.setItem(PREFS_KEY, JSON.stringify(p));
}

export function MemoToc({
  markdown,
  activeId,
  onJump,
}: {
  markdown: string;
  activeId?: string;
  onJump: (id: string) => void;
}) {
  const items: MemoHeading[] = extractMemoToc(markdown);
  if (items.length < 2) return null;
  return (
    <nav className="memo-toc" aria-label="Memo sections">
      <div className="memo-toc-title">On this page</div>
      <ul>
        {items.map((h) => (
          <li key={h.id} className={`toc-l${h.level}${activeId === h.id ? " on" : ""}`}>
            <button type="button" onClick={() => onJump(h.id)}>
              {h.text}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function ReaderToolbar({
  prefs,
  onChange,
}: {
  prefs: ReaderPrefs;
  onChange: (next: ReaderPrefs) => void;
}) {
  const set = (patch: Partial<ReaderPrefs>) => {
    const next = { ...prefs, ...patch };
    saveReaderPrefs(next);
    onChange(next);
  };
  return (
    <div className="reader-toolbar" role="toolbar" aria-label="Reading controls">
      <button
        className={`btn${prefs.readingMode ? " primary" : ""}`}
        type="button"
        onClick={() => set({ readingMode: !prefs.readingMode })}
      >
        {prefs.readingMode ? "Exit reading" : "Reading mode"}
      </button>
      <label className="reader-ctrl">
        Size
        <input
          type="range"
          min={90}
          max={130}
          step={5}
          value={Math.round(prefs.fontScale * 100)}
          onChange={(e) => set({ fontScale: Number(e.target.value) / 100 })}
          aria-label="Font size"
        />
      </label>
      <div className="reader-measure">
        {(["narrow", "default", "wide"] as const).map((m) => (
          <button
            key={m}
            type="button"
            className={`btn compact${prefs.measure === m ? " primary" : ""}`}
            onClick={() => set({ measure: m })}
          >
            {m}
          </button>
        ))}
      </div>
    </div>
  );
}
