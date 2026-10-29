// CSS shared by the three Ontology aids: the data aids' base (buttons, live region, tables that scroll in their own
// box, pickers, marks, facts and status boxes, the two-column split) plus the class tabs, the diff lines and a third
// mark tone for "missing". Tokens only (no hex): colours come from tokens.css, so the aids follow the light and
// dark themes. A mark is always a symbol and a word (✓ AGREE, ✗ DISAGREE, ? MISSING), never colour alone.
import { DATA_CSS } from '../data/dataStyle';

export const ONTOLOGY_CSS =
  DATA_CSS +
  `
.on-table { min-width: 36rem; }
.on-table td { overflow-wrap: anywhere; }
.on-table th { overflow-wrap: break-word; }
.on-mark-warn { color: var(--ink); border-left: 3px solid var(--warn); padding-left: 6px; }
.on-tabs { margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.on-tab {
  font: inherit; font-family: var(--font-mono); font-size: var(--text-small); padding: 6px 12px; min-height: 32px; color: var(--muted);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); cursor: pointer;
}
.on-tab[aria-selected="true"] { color: var(--ink); font-weight: 700; border-color: var(--accent); border-width: 2px; padding: 5px 11px; }
.on-tab:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.on-panel { display: grid; gap: 12px; min-width: 0; }
.on-def { margin: 0; padding: 10px 14px; font-size: var(--text-small); color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--role-memory); border-radius: var(--radius); }
.on-kind { font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }
.on-rule { display: block; margin-top: 4px; font-family: var(--font-mono); font-size: var(--text-label); color: var(--ink); }
.on-pre { max-height: 18rem; }
.on-wrap { white-space: pre-wrap; overflow-wrap: anywhere; }
.on-diff { margin: 0; padding: 8px 0; font-family: var(--font-mono); font-size: 0.8125rem; line-height: 1.5; color: var(--ink); background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); max-height: 22rem; }
.on-diff span { display: block; padding: 0 12px; white-space: pre; }
.on-diff-add { border-left: 3px solid var(--good); background: var(--surface); }
.on-diff-del { border-left: 3px solid var(--bad); }
.on-diff-meta { color: var(--muted); }
`;
