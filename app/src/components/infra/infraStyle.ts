// CSS shared by the five AI Infrastructure aids: the data aids' base (buttons, live region, tables that scroll in
// their own box, pickers, marks, facts and status boxes) plus the warn and info mark tones, focusable rows, the
// decision and attempt lines and the open-trace panel. Tokens only (no hex): colours come from tokens.css, so the
// aids follow the light and dark themes. A mark is always a symbol and a word (✓ hit, ○ miss, ✗ deny, ◐ warn),
// never colour alone.
import { DATA_CSS } from '../data/dataStyle';

export const INFRA_CSS =
  DATA_CSS +
  `
.in-table { min-width: 30rem; }
.in-table td { overflow-wrap: anywhere; }
.in-wide { min-width: 40rem; }
.in-mark-warn { color: var(--ink); border-left: 3px solid var(--warn); padding-left: 6px; }
.in-mark-info { color: var(--ink); border-left: 3px solid var(--line); padding-left: 6px; }
.in-row { cursor: pointer; }
.in-row[aria-selected="true"] th, .in-row[aria-selected="true"] td { background: var(--surface-2); }
.in-row[aria-selected="true"] { outline: 2px solid var(--accent); outline-offset: -2px; }
.in-row:focus-visible { outline: 3px solid var(--accent); outline-offset: -3px; }
.in-pick { margin: 0; padding: 10px 14px; font-size: var(--text-small); color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--accent); border-radius: var(--radius); overflow-wrap: anywhere; }
.in-lines { margin: 0; padding: 0 0 0 1.5rem; display: grid; gap: 4px; font-family: var(--font-mono); font-size: var(--text-small); color: var(--ink); }
.in-lines li { overflow-wrap: anywhere; }
.in-stage { font-weight: 600; }
.in-open { display: grid; gap: 8px; min-width: 0; padding: 10px 14px; background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--role-logs); border-radius: var(--radius); }
.in-open h3 { margin: 0; font-family: var(--font-mono); font-size: var(--text-body); color: var(--ink); }
.in-about { margin: 0; font-size: var(--text-small); color: var(--muted); overflow-wrap: anywhere; }
`;
