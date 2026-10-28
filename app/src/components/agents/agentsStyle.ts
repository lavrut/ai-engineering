// CSS shared by the four AI Agents aids: the MCP aids' base (buttons, live region, tables that scroll in
// their own box, step chips) plus pickers, flags and bars. Tokens only (no hex): colours come from
// tokens.css, so the aids follow the light and dark themes. Each aid adds its own rules after this.
import { BASE_CSS } from '../mcp/mcpStyle';

export const AGENTS_CSS =
  BASE_CSS +
  `
.ag-pickers { margin: 0; display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 8px 12px; border: 1px solid var(--line); border-radius: var(--radius); }
.ag-pickers legend { padding: 0 6px; font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); }
.ag-pick { display: grid; gap: 2px; min-width: 0; font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); }
.ag-pick select {
  font: inherit; font-family: var(--font-body); font-size: var(--text-small); color: var(--ink); background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--radius); padding: 4px 8px; min-height: 32px; max-width: 100%;
}
.ag-pick select:focus-visible, .ag-radio input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ag-radio { font-size: var(--text-small); color: var(--ink); cursor: pointer; min-height: 24px; }
.ag-group { display: grid; gap: 8px; min-width: 0; }
.ag-list { max-height: 14rem; overflow: auto; padding: 2px; }
.ag-flag {
  display: inline-block; margin-left: 6px; padding: 0 6px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--ink); border: 1px solid var(--line); border-radius: var(--radius); text-transform: uppercase; letter-spacing: 0.04em;
}
.ag-flag-handoff { border-color: var(--role-harness); }
.ag-flag-refused, .ag-flag-error, .ag-flag-stop { border-color: var(--bad); }
.ag-flag-written { border-color: var(--warn); }
.ag-detail { margin: 0; font-family: var(--font-mono); font-size: var(--text-small); color: var(--ink); overflow-wrap: anywhere; }
.ag-result { margin: 0; padding: 8px 12px; font-size: var(--text-small); color: var(--ink); background: var(--surface-2); border-radius: var(--radius); overflow-wrap: anywhere; }
.ag-reply { margin: 0; padding: 10px 14px; font-size: var(--text-small); color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); white-space: pre-wrap; overflow-wrap: anywhere; }
.ag-tracks { margin: 0; padding: 0; list-style: none; display: grid; gap: 12px; }
.ag-tracks > li { display: grid; gap: 4px; min-width: 0; }
.ag-name { margin: 0; font-family: var(--font-mono); font-size: var(--text-small); color: var(--ink); overflow-wrap: anywhere; }
.ag-bar { display: grid; grid-template-columns: 7.5rem minmax(0, 1fr); align-items: center; gap: 8px; font-size: var(--text-small); color: var(--ink); }
.ag-track { height: 14px; background: var(--surface-2); border: 1px solid var(--line); border-radius: 999px; overflow: hidden; }
.ag-track span { display: block; height: 100%; }
.ag-fill-single { background: var(--role-tool); }
.ag-fill-supervisor { background: var(--role-model); background-image: repeating-linear-gradient(45deg, transparent 0 4px, var(--surface) 4px 6px); }
.ag-table-wide { min-width: 44rem; }
`;
