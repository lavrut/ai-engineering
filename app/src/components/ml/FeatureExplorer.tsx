import { useEffect, useMemo, useState } from 'preact/hooks';
import { DEBOUNCE_MS, explain, pct, shortName, unpackExplorer, type FeatureExplorerProps } from '../../lib/mlAids';

const n3 = (x: number) => x.toFixed(3);

export default function FeatureExplorer({ model: packed, presets }: FeatureExplorerProps) {
  const model = useMemo(() => unpackExplorer(packed), [packed]);
  const [draft, setDraft] = useState('');
  const [text, setText] = useState('');
  const [preset, setPreset] = useState<string | null>(null);

  // Typing re-tokenizes once the reader pauses; a preset applies at once.
  useEffect(() => {
    if (draft === text) return;
    const t = setTimeout(() => setText(draft), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [draft, text]);

  const e = useMemo(() => explain(model, text), [model, text]);
  const chosen = presets.find((x) => x.id === preset && x.text === text) ?? null;
  const unknown = new Set(e.unknown);
  const top = e.classes[0];

  return (
    <section class="fx" aria-label="Feature explorer">
      <header class="fx-head">
        <p class="fx-eyebrow">Feature explorer · model {model.version}</p>
        <p class="fx-label">Runs in your browser; nothing is sent</p>
      </header>
      <p class="fx-note" role="note">
        <strong>What this page holds:</strong> {model.note} Tokens and unknown words are exact; TF-IDF weights are
        exact at the precision shown (idf is rounded to 4 significant figures); the scores are close to the
        server&apos;s, not equal.
      </p>

      <div class="fx-presets" role="group" aria-label="Test-split complaints (2022)">
        <span class="fx-presets-label">Try a 2022 test complaint:</span>
        {presets.map((x) => (
          <button
            key={x.id}
            type="button"
            class="fx-preset"
            aria-pressed={chosen?.id === x.id}
            onClick={() => {
              setPreset(x.id);
              setDraft(x.text);
              setText(x.text);
            }}
          >
            {x.id} · {shortName(x.category)}
          </button>
        ))}
      </div>

      <label class="fx-input">
        <span class="fx-input-label">Complaint text</span>
        <textarea
          rows={5}
          value={draft}
          placeholder="Type or paste a complaint, e.g. The collector keeps calling about a debt I already paid."
          onInput={(ev) => setDraft((ev.target as HTMLTextAreaElement).value)}
        />
      </label>

      <div class="fx-result" aria-live="polite">
        <p class="fx-predicted">
          {text.trim() ? `Predicted: ${top.label} (${pct(top.p)})` : 'Predicted: nothing typed yet'}
          {text.trim() && e.prediction.low_confidence ? ' · low confidence' : ''}
        </p>
        {chosen && (
          <p class="fx-truth">
            Complaint {chosen.id}, true category: {chosen.category} ({chosen.category === top.label ? 'match' : 'mismatch'})
          </p>
        )}
        <p class="fx-counts">
          {e.tokens.length} tokens · {e.terms.length} known words · {e.unknown.length} unknown
        </p>
      </div>

      <div class="fx-block">
        <p class="fx-eyebrow">Tokens, in text order</p>
        <ul class="fx-tokens" aria-label="Tokens">
          {e.tokens.map((t, i) => (
            <li key={i} class={`fx-token${unknown.has(t) ? ' is-unknown' : ''}`}>
              {t}
              {unknown.has(t) && (
                <>
                  {' '}
                  <span class="fx-unknown-tag">(unknown)</span>
                </>
              )}
            </li>
          ))}
        </ul>
        {e.unknown.length > 0 && (
          <p class="fx-unknowns">
            Unknown words ({e.unknown.length}): {e.unknown.join(', ')}. The model never saw them in training, so they add
            nothing.
          </p>
        )}
      </div>

      <div class="fx-grid">
        <div class="fx-scroll" role="region" aria-label="TF-IDF weights" tabIndex={0}>
          <table class="fx-table" aria-label="TF-IDF weight of each known word, heaviest first">
            <thead>
              <tr>
                <th scope="col">Word</th>
                <th scope="col">Count</th>
                <th scope="col">idf</th>
                <th scope="col">Count × idf</th>
                <th scope="col">Weight</th>
              </tr>
            </thead>
            <tbody>
              {e.terms.map((t) => (
                <tr key={t.term}>
                  <th scope="row">
                    {t.term}
                    {!t.hasWeights && <span class="fx-noweight">no class weights kept</span>}
                  </th>
                  <td>{t.count}</td>
                  <td>{n3(t.idf)}</td>
                  <td>{n3(t.raw)}</td>
                  <td>{n3(t.weight)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {e.terms.length === 0 && <p class="fx-empty">No known words yet.</p>}
        </div>

        <ol class="fx-classes" aria-label="Classes, most likely first">
          {e.classes.map((c, i) => (
            <li key={c.label} class={`fx-class${i === 0 && text.trim() ? ' is-top' : ''}`}>
              <p class="fx-class-head">
                <span class="fx-class-name">{c.label}</span>
                <span class="fx-class-p">{pct(c.p)}</span>
              </p>
              <span class="fx-bar" aria-hidden="true">
                <span style={{ width: `${c.p * 100}%` }} />
              </span>
              <p class="fx-class-words">
                {c.top.length ? c.top.map((w) => `${w.term} +${w.value.toFixed(2)}`).join(', ') : 'no word pushes towards it'}
              </p>
            </li>
          ))}
        </ol>
      </div>
      <p class="fx-hint">
        Weight = count × idf, scaled so the squared weights add to 1. A word&apos;s push on a class is its weight times the
        class&apos;s learned weight for it; the scores are a softmax of the pushes plus each class&apos;s intercept.
      </p>
      <style>{css}</style>
    </section>
  );
}

const css = `
.fx { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.fx-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.fx-eyebrow, .fx-input-label, .fx-presets-label {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.fx-label {
  margin: 0; padding: 3px 8px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--muted); border: 1px dashed var(--line); border-radius: var(--radius);
}
.fx-note {
  margin: 0; padding: 10px 14px; font-size: var(--text-small); color: var(--ink);
  background: color-mix(in srgb, var(--warn) 10%, var(--surface)); border: 1px solid var(--warn);
  border-left-width: 4px; border-radius: var(--radius);
}
.fx-presets { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.fx-preset {
  font: inherit; font-family: var(--font-mono); font-size: 0.8125rem; padding: 3px 10px; color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.fx-preset[aria-pressed="true"] { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.fx-input { display: grid; gap: 6px; min-width: 0; }
.fx-input textarea {
  width: 100%; box-sizing: border-box; min-width: 0; font: inherit; font-size: var(--text-small); line-height: 1.5;
  color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 8px 10px; resize: vertical;
}
.fx-preset:focus-visible, .fx-input textarea:focus-visible, .fx-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.fx-result { display: grid; gap: 2px; }
.fx-predicted { margin: 0; font-weight: 600; color: var(--ink); }
.fx-truth, .fx-counts { margin: 0; font-family: var(--font-mono); font-size: 0.8125rem; color: var(--muted); }
.fx-block { display: grid; gap: 6px; min-width: 0; }
.fx-tokens { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 4px; max-height: 9rem; overflow: auto; }
.fx-token {
  padding: 0 6px; font-family: var(--font-mono); font-size: 0.75rem; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius);
}
.fx-token.is-unknown { color: var(--muted); background: transparent; border-style: dashed; text-decoration: line-through; }
.fx-unknown-tag { text-decoration: none; display: inline-block; }
.fx-unknowns, .fx-hint, .fx-empty { margin: 0; font-size: var(--text-small); color: var(--muted); overflow-wrap: anywhere; }
.fx-empty { padding: 8px 10px; }
.fx-grid { display: grid; gap: 12px; grid-template-columns: minmax(0, 1fr); }
@media (min-width: 760px) { .fx-grid { grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); align-items: start; } }
.fx-scroll {
  min-width: 0; max-height: 22rem; overflow: auto; overscroll-behavior: contain;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
}
.fx-table { width: 100%; border-collapse: collapse; font-family: var(--font-mono); font-size: 0.8125rem; font-variant-numeric: tabular-nums; }
.fx-table th, .fx-table td { padding: 4px 8px; text-align: right; border-bottom: 1px solid var(--line); }
.fx-table thead th { position: sticky; top: 0; background: var(--surface); font-size: 0.6875rem; font-weight: 500; color: var(--muted); }
.fx-table th[scope="row"], .fx-table thead th:first-child { text-align: left; font-weight: 500; color: var(--ink); white-space: nowrap; }
.fx-noweight { display: block; white-space: normal; font-size: 0.6875rem; font-weight: 400; color: var(--muted); }
.fx-classes { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.fx-class { padding: 8px 10px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.fx-class.is-top { border-color: var(--accent); border-width: 2px; }
.fx-class-head { margin: 0; display: flex; justify-content: space-between; gap: 8px; font-size: var(--text-small); color: var(--ink); }
.fx-class-name { overflow-wrap: anywhere; }
.fx-class-p { font-family: var(--font-mono); font-weight: 600; font-variant-numeric: tabular-nums; }
.fx-bar { display: block; height: 6px; margin: 4px 0; background: var(--surface-2); border-radius: 999px; overflow: hidden; }
.fx-bar span { display: block; height: 100%; background: var(--role-model); }
.fx-class-words { margin: 0; font-family: var(--font-mono); font-size: 0.75rem; color: var(--muted); overflow-wrap: anywhere; }
`;
