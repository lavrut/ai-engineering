import { RECORD_COMMAND } from '../../lib/llmsAids';

/** The empty state for an aid that shows recorded runs, when llms/data/responses/ has none. */
export default function NoRecordedRun({ command, text }: { command?: string; text?: string } = {}) {
  return (
    <div class="nrr" role="note">
      <p class="nrr-title">No recorded run yet</p>
      <p class="nrr-text">
        {text ?? (
          <>
            Record the runs once from <code>llms/python</code>, then rebuild the app. This calls the API and costs money;
            the command prints an estimate and asks before it spends anything.
          </>
        )}
      </p>
      <pre class="nrr-cmd">
        <code>{command ?? RECORD_COMMAND}</code>
      </pre>
      <style>{css}</style>
    </div>
  );
}

const css = `
.nrr {
  display: grid; gap: 8px; padding: 14px 16px; min-width: 0;
  background: var(--surface); border: 1px dashed var(--line); border-radius: var(--radius);
}
.nrr-title { margin: 0; font-family: var(--font-display); font-size: var(--text-body); font-weight: 600; color: var(--ink); }
.nrr-text { margin: 0; font-size: var(--text-small); color: var(--muted); }
.nrr-text code { font-family: var(--font-mono); font-size: 0.875em; color: var(--ink); }
.nrr-cmd {
  margin: 0; padding: 8px 12px; overflow-x: auto; font-family: var(--font-mono); font-size: 0.8125rem;
  color: var(--ink); background: var(--surface-2); border-radius: var(--radius);
}
`;
