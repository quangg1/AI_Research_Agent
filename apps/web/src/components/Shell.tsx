import { ReactNode, useEffect, useState } from "react";
import { endpoints } from "../lib/api";

const NAV = [
  { to: "/", label: "Research", hint: "Agent loop" },
  { to: "/scenarios", label: "Scenarios", hint: "Cost engine" },
  { to: "/corpus", label: "Corpus", hint: "Source watch" },
  { to: "/workspace", label: "Workspace", hint: "Run history" },
  { to: "/settings", label: "Settings", hint: "Org & billing" },
];

export function Shell({
  path,
  go,
  llmMode,
  tracing,
  healthOk,
  children,
}: {
  path: string;
  go: (to: string) => void;
  llmMode?: string;
  tracing?: boolean;
  healthOk?: boolean | null;
  children: ReactNode;
}) {
  const [openNav, setOpenNav] = useState(false);
  const [notes, setNotes] = useState<
    Array<{ id: string; title: string; body?: string | null; run_id?: string | null }>
  >([]);
  const [bellOpen, setBellOpen] = useState(false);

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const data = await endpoints.notifications();
        if (!stop) setNotes(data.notifications.filter((n) => !n.read_at).slice(0, 20));
      } catch {
        /* ignore */
      }
    };
    load();
    const t = setInterval(load, 15000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);

  return (
    <div className="app-root">
      <div className="orb orb-a" />
      <div className="orb orb-b" />
      <div className="orb orb-c" />
      {healthOk === false && (
        <div className="degraded-banner" role="alert">
          API health check failed — research may be unavailable. Retry shortly or check Settings.
        </div>
      )}
      <header className="topbar">
        <button className="brand" type="button" onClick={() => go("/")}>
          Kiln <span>//</span> <em>systems</em>
        </button>
        <button
          className="nav-toggle"
          type="button"
          aria-expanded={openNav}
          aria-controls="primary-nav"
          onClick={() => setOpenNav((v) => !v)}
        >
          Menu
        </button>
        <nav id="primary-nav" className={`nav ${openNav ? "open" : ""}`} aria-label="Primary">
          {NAV.map((item) => {
            const on = item.to === "/" ? path === "/" : path.startsWith(item.to);
            return (
              <button
                key={item.to}
                className={`nav-item ${on ? "on" : ""}`}
                type="button"
                aria-current={on ? "page" : undefined}
                onClick={() => {
                  setOpenNav(false);
                  go(item.to);
                }}
              >
                <span>{item.label}</span>
                <small>{item.hint}</small>
              </button>
            );
          })}
        </nav>
        <div className="pills">
          <div className="bell-wrap">
            <button
              className="pill"
              type="button"
              aria-label={`Notifications${notes.length ? `, ${notes.length} unread` : ""}`}
              onClick={() => setBellOpen((v) => !v)}
            >
              Alerts{notes.length ? ` (${notes.length})` : ""}
            </button>
            {bellOpen && (
              <div className="bell-panel" role="menu">
                {notes.length === 0 && <p className="idle">No unread notifications</p>}
                {notes.map((n) => (
                  <button
                    key={n.id}
                    className="bell-item"
                    type="button"
                    onClick={async () => {
                      await endpoints.readNotification(n.id);
                      setNotes((prev) => prev.filter((x) => x.id !== n.id));
                      if (n.run_id) go(`/?run=${n.run_id}`);
                      setBellOpen(false);
                    }}
                  >
                    <strong>{n.title}</strong>
                    <span>{n.body}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <span className={`pill ${llmMode === "gemini" ? "on" : ""}`}>{llmMode || "offline"}</span>
          {tracing ? <span className="pill on">traced</span> : <span className="pill">local</span>}
        </div>
      </header>
      <div className="shell">{children}</div>
    </div>
  );
}
