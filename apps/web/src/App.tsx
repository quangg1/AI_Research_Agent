import { useEffect, useState } from "react";
import {
  ClerkProvider,
  SignedIn,
  SignedOut,
  SignInButton,
  UserButton,
  OrganizationSwitcher,
  useAuth,
  useOrganization,
} from "@clerk/clerk-react";
import { Shell } from "./components/Shell";
import { CorpusPage } from "./pages/Corpus";
import { ResearchPage } from "./pages/Research";
import { ScenariosPage } from "./pages/Scenarios";
import { WorkspacePage } from "./pages/Workspace";
import { SettingsPage } from "./pages/Settings";
import { SharePage } from "./pages/Share";
import { endpoints, setTokenProvider } from "./lib/api";

function currentPath() {
  return window.location.pathname || "/";
}

const clerkKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;
const authMode = (import.meta.env.VITE_AUTH_MODE || "dev").toLowerCase();

function ClerkBridge({ children }: { children: React.ReactNode }) {
  const { getToken, isSignedIn } = useAuth();
  const { organization } = useOrganization();
  useEffect(() => {
    setTokenProvider(async () => (isSignedIn ? getToken() : null));
  }, [getToken, isSignedIn]);
  useEffect(() => {
    if (organization?.id) localStorage.setItem("kiln_org_id", organization.id);
  }, [organization?.id]);
  return <>{children}</>;
}

function AppInner() {
  const [path, setPath] = useState(currentPath);
  const [statusInfo, setStatusInfo] = useState<{ llm_mode?: string; tracing?: boolean }>({});
  const [seedQuery, setSeedQuery] = useState("");
  const [healthOk, setHealthOk] = useState<boolean | null>(null);
  const [consent, setConsent] = useState(() => localStorage.getItem("kiln_consent") === "1");

  useEffect(() => {
    const onPop = () => setPath(currentPath());
    window.addEventListener("popstate", onPop);
    endpoints
      .status()
      .then(setStatusInfo)
      .catch(() => undefined);
    endpoints
      .health()
      .then(() => setHealthOk(true))
      .catch(() => setHealthOk(false));
    const t = setInterval(() => {
      endpoints
        .health()
        .then(() => setHealthOk(true))
        .catch(() => setHealthOk(false));
    }, 30000);
    return () => {
      window.removeEventListener("popstate", onPop);
      clearInterval(t);
    };
  }, []);

  function go(to: string, query?: string) {
    if (query) setSeedQuery(query);
    window.history.pushState({}, "", to);
    setPath(to.split("?")[0] || "/");
  }

  const route = path.split("?")[0] || "/";

  if (route.startsWith("/share/")) {
    const token = route.replace("/share/", "");
    return (
      <Shell path={route} go={go} llmMode={statusInfo.llm_mode} tracing={statusInfo.tracing} healthOk={healthOk}>
        <SharePage token={token} />
      </Shell>
    );
  }

  return (
    <Shell path={route} go={go} llmMode={statusInfo.llm_mode} tracing={statusInfo.tracing} healthOk={healthOk}>
      {authMode === "clerk" && (
        <div className="auth-bar">
          <SignedOut>
            <SignInButton mode="modal">
              <button className="btn primary" type="button">
                Sign in
              </button>
            </SignInButton>
          </SignedOut>
          <SignedIn>
            <OrganizationSwitcher hidePersonal afterSelectOrganizationUrl="/" />
            <UserButton />
          </SignedIn>
        </div>
      )}
      {!consent && (
        <div className="consent-banner" role="dialog" aria-label="Privacy notice">
          <p>
            Kiln stores research queries and reports in your organization workspace. Do not submit secrets or personal
            data you are not authorized to process.
          </p>
          <button
            className="btn primary"
            type="button"
            onClick={() => {
              localStorage.setItem("kiln_consent", "1");
              setConsent(true);
            }}
          >
            I understand
          </button>
        </div>
      )}
      {route.startsWith("/scenarios") ? (
        <ScenariosPage go={go} />
      ) : route.startsWith("/corpus") ? (
        <CorpusPage />
      ) : route.startsWith("/workspace") ? (
        <WorkspacePage go={go} />
      ) : route.startsWith("/settings") ? (
        <SettingsPage />
      ) : route === "/" || route.startsWith("/research") ? (
        <ResearchPage go={go} initialQuery={seedQuery} />
      ) : (
        <section className="panel">
          <h2>Page not found</h2>
          <p className="idle">Unknown route. Return to Research.</p>
          <button className="btn primary" type="button" onClick={() => go("/")}>
            Go to Research
          </button>
        </section>
      )}
    </Shell>
  );
}

export function App() {
  if (authMode === "clerk" && clerkKey) {
    return (
      <ClerkProvider publishableKey={clerkKey}>
        <ClerkBridge>
          <AppInner />
        </ClerkBridge>
      </ClerkProvider>
    );
  }
  return <AppInner />;
}
