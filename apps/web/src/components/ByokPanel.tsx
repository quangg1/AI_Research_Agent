import {
  byokReady,
  persistByok,
  PROVIDER_META,
  splitApiKeys,
  visitorKeyList,
  visitorKeys,
  type ByokState,
  type LlmProvider,
} from "../lib/byok";

export function ByokPanel({
  value,
  onChange,
  creditsForced = false,
  platformProviders,
}: {
  value: ByokState;
  onChange: (next: ByokState) => void;
  creditsForced?: boolean;
  platformProviders?: Record<string, boolean>;
}) {
  const keys = visitorKeys(value);
  const pasted = visitorKeyList(value);
  const ready = byokReady(value, creditsForced);
  const hostedAny = Object.values(platformProviders || {}).some(Boolean);

  function patch(partial: Partial<ByokState>) {
    const next = { ...value, ...partial };
    onChange(next);
    persistByok(next);
  }

  function setKey(id: LlmProvider, apiKey: string) {
    const nextKeys = { ...keys, [id]: apiKey };
    patch({
      keys: nextKeys,
      apiKey: id === value.provider ? apiKey : value.apiKey,
      acknowledged: pasted.length > 0 || apiKey.trim().length > 0 ? value.acknowledged : false,
    });
  }

  return (
    <div className="byok">
      <div className="byok-warn" role="note">
        {creditsForced ? (
          <>
            <strong>Every configured provider ran out of credits.</strong> Paste at least one of your
            own keys. Kiln tries them in order for this run and never writes them to the database,
            logs, or job queue.
          </>
        ) : (
          <>
            <strong>Pick who to try first.</strong> Kiln uses your pasted keys, then host keys in
            env. Paste several keys for one provider with <code>;</code> between them. If a key is
            down or out of credits, the run continues with the next one. Keys are never stored.
          </>
        )}
      </div>
      <div className="byok-providers" role="radiogroup" aria-label="Preferred model provider">
        {(Object.keys(PROVIDER_META) as LlmProvider[]).map((id) => (
          <button
            key={id}
            type="button"
            className={`byok-choice ${value.provider === id ? "on" : ""}`}
            onClick={() => patch({ provider: id, apiKey: keys[id] })}
          >
            {PROVIDER_META[id].label}
            <small>
              {splitApiKeys(keys[id]).length > 1
                ? `${splitApiKeys(keys[id]).length} keys`
                : splitApiKeys(keys[id]).length === 1
                  ? "your key"
                  : platformProviders?.[id]
                    ? "hosted"
                    : "empty"}
            </small>
          </button>
        ))}
      </div>
      <div className="byok-grid byok-keys">
        {(Object.keys(PROVIDER_META) as LlmProvider[]).map((id) => (
          <label key={id}>
            {PROVIDER_META[id].label} key
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={keys[id]}
              placeholder={PROVIDER_META[id].placeholder}
              onChange={(e) => setKey(id, e.target.value)}
            />
          </label>
        ))}
      </div>
      <p className="byok-hint">
        Optional. Multiple keys for one box: <code>key-one;key-two</code>. Leave a field blank to use Kiln’s hosted key for that provider
        {hostedAny ? "" : " (none configured on this server yet)"}.{" "}
        {(Object.keys(PROVIDER_META) as LlmProvider[]).map((id, i) => (
          <span key={id}>
            {i > 0 ? " · " : ""}
            <a href={PROVIDER_META[id].docs} target="_blank" rel="noreferrer">
              {PROVIDER_META[id].label}
            </a>
          </span>
        ))}
      </p>
      {(pasted.length > 0 || creditsForced) && (
        <label className="byok-check">
          <input
            type="checkbox"
            checked={value.acknowledged}
            onChange={(e) => patch({ acknowledged: e.target.checked })}
          />
          I understand visitor keys are sent only for this run, are not stored by Kiln, and usage is
          billed to my provider accounts.
        </label>
      )}
      <div className="byok-actions">
        {!ready && creditsForced && (
          <span className="byok-block">Acknowledge and paste at least one key to continue.</span>
        )}
        {pasted.length > 0 && (
          <button
            className="btn"
            type="button"
            onClick={() => {
              const next = {
                ...value,
                apiKey: "",
                keys: { gemini: "", openai: "", grok: "" },
                acknowledged: false,
              };
              persistByok(next);
              onChange(next);
            }}
          >
            Clear keys
          </button>
        )}
      </div>
    </div>
  );
}
