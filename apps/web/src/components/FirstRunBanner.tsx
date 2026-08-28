import { FIRST_RUN_TEMPLATES } from "../lib/firstRun";

export function FirstRunBanner({
  hostedAny,
  byokRequired,
  onPick,
  onDismiss,
}: {
  hostedAny: boolean;
  byokRequired: boolean;
  onPick: (text: string) => void;
  onDismiss: () => void;
}) {
  return (
    <section className="first-run-banner" aria-label="Getting started">
      <div className="first-run-head">
        <div>
          <div className="hero-kicker">Getting started</div>
          <h2>Run your first deep research</h2>
          <p className="sub">
            {byokRequired
              ? "This server requires your model API key — paste one in the panel below and tick the billing checkbox."
              : hostedAny
                ? "Hosted keys are configured — pick a provider and start with an example, or paste your own key for billing on your account."
                : "No hosted keys here — paste a Gemini, OpenAI, or Grok key below and confirm billing before you run."}
          </p>
        </div>
        <button className="btn compact" type="button" onClick={onDismiss} aria-label="Dismiss getting started">
          Dismiss
        </button>
      </div>
      <div className="first-run-templates">
        <p className="first-run-label">Try a template</p>
        <div className="suggest">
          {FIRST_RUN_TEMPLATES.map((ex) => (
            <button type="button" className="badge" key={ex.text} onClick={() => onPick(ex.text)}>
              {ex.tag}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
