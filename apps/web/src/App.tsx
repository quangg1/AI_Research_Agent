import { useEffect, useState } from "react";
import { Shell } from "./components/Shell";
import { CorpusPage } from "./pages/Corpus";
import { ResearchPage } from "./pages/Research";
import { ScenariosPage } from "./pages/Scenarios";
import { WorkspacePage } from "./pages/Workspace";
import { endpoints } from "./lib/api";

function currentPath() {
  return window.location.pathname || "/";
}

export function App() {
  const [path, setPath] = useState(currentPath);
  const [statusInfo, setStatusInfo] = useState<{ llm_mode?: string; tracing?: boolean }>({});
  const [seedQuery, setSeedQuery] = useState("");

  useEffect(() => {
    const onPop = () => setPath(currentPath());
    window.addEventListener("popstate", onPop);
    endpoints.status().then(setStatusInfo).catch(() => undefined);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function go(to: string, query?: string) {
    if (query) setSeedQuery(query);
    window.history.pushState({}, "", to);
    setPath((to.split("?")[0] || "/"));
  }

  const route = path.split("?")[0] || "/";

  return (
    <Shell path={route} go={go} llmMode={statusInfo.llm_mode} tracing={statusInfo.tracing}>
      {route.startsWith("/scenarios") ? (
        <ScenariosPage go={go} />
      ) : route.startsWith("/corpus") ? (
        <CorpusPage />
      ) : route.startsWith("/workspace") ? (
        <WorkspacePage go={go} />
      ) : (
        <ResearchPage go={go} initialQuery={seedQuery} />
      )}
    </Shell>
  );
}
