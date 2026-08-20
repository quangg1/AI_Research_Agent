import { useEffect, useState } from "react";
import { endpoints } from "../lib/api";

export function SettingsPage() {
  const [billing, setBilling] = useState<any>(null);
  const [keys, setKeys] = useState<any[]>([]);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [me, setMe] = useState<any>(null);

  async function load() {
    try {
      const [b, k, m] = await Promise.all([
        endpoints.billing(),
        endpoints.listApiKeys().catch(() => []),
        endpoints.me(),
      ]);
      setBilling(b);
      setKeys(Array.isArray(k) ? k : []);
      setMe(m);
    } catch (err: any) {
      setError(err.message || "Settings unavailable");
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function checkout() {
    setBusy(true);
    setError("");
    try {
      const data = await endpoints.checkout({
        success_url: `${window.location.origin}/settings?billing=success`,
        cancel_url: `${window.location.origin}/settings?billing=cancel`,
      });
      if (data.url) window.location.href = String(data.url);
    } catch (err: any) {
      setError(err.message || "Checkout failed");
    } finally {
      setBusy(false);
    }
  }

  async function portal() {
    setBusy(true);
    try {
      const data = await endpoints.portal({ return_url: `${window.location.origin}/settings` });
      if (data.url) window.location.href = String(data.url);
    } catch (err: any) {
      setError(err.message || "Portal failed");
    } finally {
      setBusy(false);
    }
  }

  async function createKey() {
    setBusy(true);
    try {
      const data = await endpoints.createApiKey("Research API");
      setNewKey(String(data.secret || ""));
      await load();
    } catch (err: any) {
      setError(err.message || "Could not create key");
    } finally {
      setBusy(false);
    }
  }

  async function exportData() {
    setBusy(true);
    try {
      const data = await endpoints.exportOrg();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `kiln-export-${Date.now()}.json`;
      a.click();
    } catch (err: any) {
      setError(err.message || "Export failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="hero">
        <div className="hero-kicker">Organization</div>
        <h1>Settings</h1>
        <p>Plan, usage, API keys, and data export for your workspace.</p>
      </section>
      {error && <p className="err">{error}</p>}
      {me && (
        <section className="panel">
          <h3>Session</h3>
          <p className="idle">
            User {me.userId} · Org {me.orgId} · Role {me.role} · Auth {me.authMode}
          </p>
        </section>
      )}
      {billing && (
        <section className="panel">
          <h3>Billing & quota</h3>
          <div className="stat-row">
            <div className="stat">
              <strong>{billing.plan}</strong>
              <span>plan</span>
            </div>
            <div className="stat">
              <strong>
                {billing.usage_this_month}/{billing.monthly_run_quota}
              </strong>
              <span>runs this month</span>
            </div>
            <div className="stat">
              <strong>{billing.remaining}</strong>
              <span>remaining</span>
            </div>
            <div className="stat">
              <strong>{billing.retention_days}d</strong>
              <span>retention</span>
            </div>
          </div>
          <div className="usage-bar" aria-label="Monthly usage">
            <div
              className="usage-fill"
              style={{
                width: `${Math.min(
                  100,
                  (Number(billing.usage_this_month) / Math.max(1, Number(billing.monthly_run_quota))) * 100,
                )}%`,
              }}
            />
          </div>
          <div className="btn-row" style={{ marginTop: 12 }}>
            <button className="btn primary" type="button" disabled={busy} onClick={checkout}>
              Upgrade to Pro
            </button>
            <button className="btn" type="button" disabled={busy} onClick={portal}>
              Manage billing
            </button>
            <button className="btn" type="button" disabled={busy} onClick={exportData}>
              Export org data
            </button>
          </div>
        </section>
      )}
      <section className="panel">
        <h3>API keys</h3>
        <p className="idle">Scoped keys for programmatic access. Shown once at creation.</p>
        {newKey && (
          <p className="reuse-note">
            New key (copy now): <code>{newKey}</code>
          </p>
        )}
        <div className="btn-row">
          <button className="btn primary" type="button" disabled={busy} onClick={createKey}>
            Create key
          </button>
        </div>
        <ul className="timeline">
          {keys.map((k) => (
            <li key={k.id}>
              <span className="tag">{k.key_prefix}…</span>
              <span>{k.name}</span>
              {!k.revoked_at && (
                <button
                  className="btn"
                  type="button"
                  onClick={async () => {
                    await endpoints.revokeApiKey(String(k.id));
                    await load();
                  }}
                >
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
