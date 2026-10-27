// CSS shared by the five MCP aids. Tokens only (no hex): colours come from tokens.css, so the aids
// follow the light and dark themes. Each aid adds its own rules after this.
export const BASE_CSS = `
.mc { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.mc-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 24px; }
.mc-eyebrow {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.mc-label {
  margin: 0; padding: 3px 8px; font-family: var(--font-mono); font-size: var(--text-label);
  color: var(--muted); border: 1px dashed var(--line); border-radius: var(--radius);
}
.mc-buttons { display: flex; flex-wrap: wrap; gap: 8px; }
.mc-btn {
  font: inherit; font-size: var(--text-small); font-weight: 500; padding: 6px 14px; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); cursor: pointer;
}
.mc-btn-primary { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.mc-btn:disabled { color: var(--muted); background: transparent; border-color: var(--line); cursor: default; }
.mc-btn:focus-visible, .mc-focus:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.mc-live { margin: 0; font-size: var(--text-small); color: var(--ink); }
.mc-hint { margin: 0; font-size: var(--text-small); color: var(--muted); }
.mc-note {
  margin: 0; padding: 10px 14px; font-size: var(--text-small); color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-left: 4px solid var(--warn); border-radius: var(--radius);
}
.mc-h { margin: 0; font-family: var(--font-display); font-size: var(--text-body); font-weight: 600; color: var(--ink); }
.mc-scroll { min-width: 0; max-width: 100%; overflow: auto; }
.mc-pre {
  margin: 0; padding: 10px 12px; font-family: var(--font-mono); font-size: 0.8125rem; line-height: 1.5; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); white-space: pre; max-height: 22rem;
}
.mc-pre-label { margin: 0 0 4px; font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted); }
.mc-table { width: 100%; border-collapse: collapse; font-size: var(--text-small); font-variant-numeric: tabular-nums; background: var(--surface); border: 1px solid var(--line); }
.mc-table th, .mc-table td { padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }
.mc-table thead th { font-family: var(--font-mono); font-size: var(--text-label); font-weight: 500; color: var(--muted); }
.mc-table th[scope="row"] { font-weight: 500; color: var(--ink); }
.mc-table td { color: var(--ink); }
.mc-num { text-align: right !important; font-family: var(--font-mono); white-space: nowrap; }
.mc-steps { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 6px; }
.mc-steps li {
  padding: 3px 10px; font-family: var(--font-mono); font-size: var(--text-label); color: var(--muted);
  border: 1px solid var(--line); border-radius: var(--radius); overflow-wrap: anywhere;
}
.mc-steps li[aria-current="step"] { color: var(--ink); font-weight: 700; border-color: var(--accent); border-width: 2px; padding: 2px 9px; }
.mc-cmd { margin: 0; padding: 8px 12px; overflow-x: auto; font-family: var(--font-mono); font-size: var(--text-small); color: var(--ink); background: var(--surface-2); border-radius: var(--radius); white-space: pre; }
@media (max-width: 599px) { .mc-btn { padding: 10px 16px; } }
`;
