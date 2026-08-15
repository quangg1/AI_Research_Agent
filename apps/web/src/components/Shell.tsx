import { ReactNode } from "react";

const NAV = [
  { to: "/", label: "Research", hint: "Agent loop" },
  { to: "/scenarios", label: "Scenarios", hint: "Cost engine" },
  { to: "/corpus", label: "Corpus", hint: "Source watch" },
  { to: "/workspace", label: "Workspace", hint: "Run history" },
];

export function Shell({
  path,
  go,
  llmMode,
  tracing,
  children,
}: {
  path: string;
  go: (to: string) => void;
  llmMode?: string;
  tracing?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="app-root">
      <div className="orb orb-a" />
      <div className="orb orb-b" />
      <div className="orb orb-c" />
      <header className="topbar">
        <button className="brand" type="button" onClick={() => go("/")}>
          Kiln <span>//</span> <em>systems</em>
        </button>
        <nav className="nav">
          {NAV.map((item) => {
            const on = item.to === "/" ? path === "/" : path.startsWith(item.to);
            return (
              <button key={item.to} className={`nav-item ${on ? "on" : ""}`} type="button" onClick={() => go(item.to)}>
                <span>{item.label}</span>
                <small>{item.hint}</small>
              </button>
            );
          })}
        </nav>
        <div className="pills">
          <span className={`pill ${llmMode === "gemini" ? "on" : ""}`}>{llmMode || "offline"}</span>
          {tracing ? <span className="pill on">traced</span> : <span className="pill">local</span>}
        </div>
      </header>
      <div className="shell">{children}</div>
    </div>
  );
}
