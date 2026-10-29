// CSS shared by the four Data Fabric & Data Contracts aids: the agents aids' base (the MCP buttons, live region,
// tables that scroll in their own box, step chips, pickers) plus pass and fail marks, a facts list, status boxes
// and the two-version layout. Tokens only (no hex): colours come from tokens.css, so the aids follow the light
// and dark themes. A mark is always a symbol and a word (✓ OK, ✗ FAIL), never colour alone.
import { AGENTS_CSS } from '../agents/agentsStyle';

export const DATA_CSS =
  AGENTS_CSS +
  `
.dt-table { min-width: 34rem; }
.dt-table td { overflow-wrap: anywhere; }
.dt-mark { font-family: var(--font-mono); font-size: var(--text-label); font-weight: 600; white-space: nowrap; }
.dt-mark-ok { color: var(--ink); border-left: 3px solid var(--good); padding-left: 6px; }
.dt-mark-fail { color: var(--ink); border-left: 3px solid var(--bad); padding-left: 6px; }
.dt-row-fail th, .dt-row-fail td { background: var(--surface-2); }
.dt-facts { margin: 0; padding: 0; list-style: none; display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; }
.dt-facts li { display: grid; grid-template-columns: 7.5rem minmax(0, 1fr); gap: 8px; font-size: var(--text-small); color: var(--ink); }
.dt-key { font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); }
.dt-val { font-family: var(--font-mono); overflow-wrap: anywhere; }
.dt-status { display: grid; gap: 4px; padding: 10px 14px; background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--good); border-radius: var(--radius); }
.dt-status-bad { border-left-color: var(--bad); }
.dt-status p { margin: 0; font-size: var(--text-small); color: var(--ink); overflow-wrap: anywhere; }
.dt-verdict { font-family: var(--font-display); font-size: var(--text-body); font-weight: 600; }
.dt-split { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr)); gap: 12px; min-width: 0; }
.dt-split > * { min-width: 0; display: grid; gap: 8px; align-content: start; }
.dt-version { padding: 10px 14px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.dt-version h3 { margin: 0; font-family: var(--font-mono); font-size: var(--text-body); color: var(--ink); }
.dt-version ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 6px; }
.dt-version li, .dt-version p { margin: 0; font-size: var(--text-small); color: var(--ink); overflow-wrap: anywhere; }
.dt-deprecated { border-style: dashed; }
.dt-edge { margin: 0; padding: 8px 12px; font-family: var(--font-mono); font-size: var(--text-small); color: var(--ink); background: var(--surface-2); border-left: 4px solid var(--role-dataset); border-radius: var(--radius); overflow-wrap: anywhere; }
.dt-quote { margin: 0; padding: 0 0 0 12px; font-size: var(--text-small); color: var(--muted); border-left: 2px solid var(--line); }
`;
