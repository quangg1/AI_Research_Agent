import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { endpoints } from "../lib/api";

export function SharePage({ token }: { token: string }) {
  const [run, setRun] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    endpoints
      .getShared(token)
      .then(setRun)
      .catch((err: any) => setError(err.message || "Share unavailable"));
  }, [token]);

  const body = run?.agent?.values?.report?.body_markdown || "";

  return (
    <>
      <section className="hero">
        <div className="hero-kicker">Shared research</div>
        <h1>{run?.title || run?.query || "Shared memo"}</h1>
        <p>Read-only link. Sign in to start your own research.</p>
      </section>
      {error && <p className="err">{error}</p>}
      {run && (
        <section className="panel memo-panel">
          <article className="memo md">
            {body ? (
              <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
            ) : (
              <p className="idle">Status: {run.status}. Full memo not available yet.</p>
            )}
          </article>
        </section>
      )}
    </>
  );
}
