import { useMemo, useState } from 'preact/hooks';
import { filterOptions, filterTickets, type FilterField, type TicketFilter, type TicketRow } from '../../lib/llmsAids';

type Props = { tickets: TicketRow[] };

const GROUPS: { field: FilterField; label: string }[] = [
  { field: 'category', label: 'Category' },
  { field: 'priority', label: 'Priority' },
  { field: 'split', label: 'Split' },
  { field: 'tag', label: 'Tag' },
];

export default function DatasetBrowser({ tickets }: Props) {
  const [filter, setFilter] = useState<TicketFilter>({});
  const options = useMemo(() => filterOptions(tickets), [tickets]);
  const shown = filterTickets(tickets, filter);
  const active = Object.values(filter).some(Boolean);

  const toggle = (field: FilterField, value: string) =>
    setFilter((f) => ({ ...f, [field]: f[field] === value ? null : value }));

  return (
    <section class="db" aria-label="Dataset browser">
      <header class="db-head">
        <p class="db-eyebrow">Dataset browser</p>
        <p class="db-count" aria-live="polite">
          {shown.length} of {tickets.length} tickets
        </p>
      </header>

      <div class="db-filters">
        {GROUPS.map((g) => (
          <fieldset key={g.field} class="db-group">
            <legend>{g.label}</legend>
            <div class="db-toggles">
              {options[g.field].map((v) => (
                <button
                  key={v}
                  type="button"
                  class="db-toggle"
                  aria-pressed={filter[g.field] === v}
                  onClick={() => toggle(g.field, v)}
                >
                  {v}
                </button>
              ))}
            </div>
          </fieldset>
        ))}
        <button type="button" class="db-clear" disabled={!active} onClick={() => setFilter({})}>
          Clear filters
        </button>
      </div>

      <div class="db-scroll" tabIndex={0} role="region" aria-label="Tickets">
        {shown.length ? (
          <ul class="db-list">
            {shown.map((t) => (
              <li key={t.id} class="db-item">
                <p class="db-meta">
                  <span class="db-id">{t.id}</span>
                  <span class="db-chip">{t.category}</span>
                  <span class="db-chip">{t.priority}</span>
                  <span class="db-chip db-chip-split">{t.split}</span>
                  {t.tags.map((tag) => (
                    <span key={tag} class="db-tag">
                      {tag}
                    </span>
                  ))}
                </p>
                <p class="db-text">{t.text}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p class="db-empty">No ticket matches these filters.</p>
        )}
      </div>
      <style>{css}</style>
    </section>
  );
}

const css = `
.db { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.db-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 24px; }
.db-eyebrow, .db-group legend {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.db-count { margin: 0; font-family: var(--font-mono); font-size: var(--text-small); font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.db-filters {
  display: grid; gap: 10px; padding: 12px 14px; min-width: 0;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius);
}
.db-group { margin: 0; padding: 0; border: 0; min-width: 0; display: grid; grid-template-columns: 5.5rem minmax(0, 1fr); gap: 6px 10px; align-items: baseline; }
.db-group legend { float: left; padding: 0; }
@media (max-width: 599px) { .db-group { grid-template-columns: minmax(0, 1fr); } .db-group legend { float: none; } }
.db-toggles { display: flex; flex-wrap: wrap; gap: 6px; }
.db-toggle, .db-clear {
  font: inherit; font-family: var(--font-mono); font-size: 0.8125rem; padding: 3px 10px; color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: 999px; cursor: pointer;
}
.db-toggle[aria-pressed="true"] { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.db-clear { justify-self: start; border-radius: var(--radius); font-family: var(--font-body); font-size: var(--text-small); padding: 4px 12px; }
.db-clear:disabled { color: var(--muted); background: transparent; cursor: default; }
.db-toggle:focus-visible, .db-clear:focus-visible, .db-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.db-scroll {
  max-height: 28rem; overflow: auto; overscroll-behavior: contain;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
}
.db-list { margin: 0; padding: 0 14px; list-style: none; }
.db-item { padding: 10px 0; border-bottom: 1px solid var(--line); }
.db-item:last-child { border-bottom: 0; }
.db-meta { margin: 0 0 4px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; }
.db-id { font-family: var(--font-mono); font-size: 0.8125rem; font-weight: 600; color: var(--ink); margin-right: 4px; }
.db-chip, .db-tag {
  padding: 0 7px; font-family: var(--font-mono); font-size: 0.75rem; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: 999px;
}
.db-chip-split { color: var(--muted); }
.db-tag { color: var(--role-dataset); background: transparent; border-color: currentColor; }
.db-text { margin: 0; font-size: var(--text-small); line-height: 1.55; color: var(--ink); overflow-wrap: anywhere; }
.db-empty { margin: 0; padding: 14px; font-size: var(--text-small); color: var(--muted); }
`;
