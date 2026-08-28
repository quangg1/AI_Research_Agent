type ThreadRun = { id: string; query: string; status: string };

export function ResearchThread({
  parent,
  children,
  currentRunId,
  onOpen,
}: {
  parent?: ThreadRun | null;
  children?: ThreadRun[];
  currentRunId?: string;
  onOpen: (id: string) => void;
}) {
  if (!parent && !(children && children.length)) return null;
  return (
    <nav className="research-thread" aria-label="Research thread">
      <div className="thread-label">Thread</div>
      {parent ? (
        <button type="button" className="thread-item parent" onClick={() => onOpen(parent.id)}>
          <span className="thread-role">Continues from</span>
          <span className="thread-query">{truncate(parent.query)}</span>
          <span className="thread-status">{parent.status}</span>
        </button>
      ) : null}
      {!!children?.length && (
        <div className="thread-children">
          <span className="thread-role">Follow-ups</span>
          {children.map((child) => (
            <button
              key={child.id}
              type="button"
              className={`thread-item${child.id === currentRunId ? " on" : ""}`}
              onClick={() => onOpen(child.id)}
            >
              <span className="thread-query">{truncate(child.query)}</span>
              <span className="thread-status">{child.status}</span>
            </button>
          ))}
        </div>
      )}
    </nav>
  );
}

function truncate(text: string, max = 96) {
  const t = (text || "").trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max)}…`;
}
