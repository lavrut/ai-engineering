// @vitest-environment jsdom
// The Data Fabric track's catalogue aids: CatalogExplorer (lesson 2) and QualityReport (lesson 3). Every number
// an aid shows is pinned here as a literal taken from data/data/derived/catalog.json and verdicts.json
// (.venv-data/bin/python data/python/catalog.py and quality.py write them; verdicts.json's ages are as of its
// pinned as_of, ruling D10).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadDataData } from '../src/lib/dataData';
import {
  DATA_COMMANDS,
  catalogExplorerProps,
  count,
  datasetSummary,
  days,
  entryStatus,
  entrySummary,
  listText,
  qualityReportProps,
  ruleSection,
  tally,
  verdictView,
} from '../src/lib/dataAids';
import CatalogExplorer from '../src/components/data/CatalogExplorer';
import QualityReport from '../src/components/data/QualityReport';

const here = dirname(fileURLToPath(import.meta.url));
const data = loadDataData();
const live = () => document.querySelector('[aria-live="polite"]')?.textContent;
const select = (label: string) => screen.getByLabelText(label) as HTMLSelectElement;

afterEach(() => cleanup());

// ── The modules ─────────────────────────────────────────────────────────────
const DATA_LIB = ['dataAids.ts', 'dataAidsTypes.ts', 'dataAidsCatalog.ts', 'dataAidsQuality.ts', 'dataAidsContract.ts', 'dataAidsLineage.ts', 'dataData.ts'];
const ISLANDS = ['CatalogExplorer.tsx', 'QualityReport.tsx', 'ContractViewer.tsx', 'LineageDiff.tsx'];

describe('data aid modules', () => {
  it('are split per aid, each under 400 lines, and dataAids.ts only re-exports them', () => {
    for (const f of DATA_LIB) expect(readFileSync(join(here, '../src/lib', f), 'utf8').split('\n').length, f).toBeLessThan(400);
    const barrel = readFileSync(join(here, '../src/lib/dataAids.ts'), 'utf8');
    expect(barrel.match(/^export \* from '\.\/dataAids\w+';$/gm)).toHaveLength(5);
  });

  it('islands import only the browser-safe barrel, never the build-time loader', () => {
    for (const f of ISLANDS) {
      const src = readFileSync(join(here, '../src/components/data', f), 'utf8');
      expect(src, f).not.toMatch(/dataData|codeSourceText/);
      expect(src, f).toMatch(/from '\.\.\/\.\.\/lib\/dataAids'/);
      expect(src, f).toMatch(/aria-live="polite"/);
    }
  });

  it('keeps the islands thin', () => {
    for (const f of ISLANDS) expect(readFileSync(join(here, '../src/components/data', f), 'utf8').split('\n').length, f).toBeLessThan(250);
  });

  it('puts every table in its own scroll box, with a minimum width, so 375 px never scrolls the page', () => {
    for (const f of ISLANDS) {
      const src = readFileSync(join(here, '../src/components/data', f), 'utf8');
      const tables = src.match(/<table\b/g) ?? [];
      const boxes = src.match(/<div class="mc-scroll mc-focus" role="region" aria-label="[^"]+" tabIndex=\{0\}>\s*<table\b/g) ?? [];
      expect(boxes.length, f).toBe(tables.length);
    }
    const style = readFileSync(join(here, '../src/components/data/dataStyle.ts'), 'utf8');
    expect(style).toMatch(/\.dt-table \{ min-width: \d+rem; \}/);
    expect(style).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});

describe('formats and shared text', () => {
  it('groups counts, says days with one decimal as the JSON has it, and joins lists in words', () => {
    expect(count(3600)).toBe('3,600');
    expect(count(4609144)).toBe('4,609,144');
    expect(days(1.8)).toBe('1.8 days');
    expect(days(2)).toBe('2 days');
    expect(days(1)).toBe('1 day');
    expect(days(0)).toBe('0 days');
    expect(days(null)).toBe('unknown');
    expect(listText([])).toBe('no one');
    expect(listText(['mcp'])).toBe('mcp');
    expect(listText(['app', 'ml'])).toBe('app and ml');
    expect(listText(['agents', 'app', 'mcp'])).toBe('agents, app and mcp');
  });

  it('marks pass and fail by text, not only by colour', () => {
    expect(verdictView({ rule: 'required', field: 'urgency', ok: false, detail: 'row 1: missing (+59 more)' })).toEqual({
      mark: 'FAIL', ok: false, rule: 'required', field: 'urgency', detail: 'row 1: missing (+59 more)', section: 'schema',
    });
    expect(verdictView({ rule: 'row_count', field: null, ok: true, detail: '60 rows (min 60, max 60)' })).toMatchObject({ mark: 'OK', field: '(whole file)', section: 'quality' });
  });

  it('maps each rule to the contract section that declares it', () => {
    for (const r of ['required', 'type', 'enum', 'pattern', 'extra_field']) expect(ruleSection(r), r).toBe('schema');
    for (const r of ['row_count', 'unique', 'enum_coverage']) expect(ruleSection(r), r).toBe('quality');
    expect(ruleSection('freshness')).toBe('servicing');
    expect(ruleSection('parse')).toBeNull();
    expect(ruleSection('contract')).toBeNull();
  });

  it('names the commands that rewrite the derived files', () => {
    expect(DATA_COMMANDS).toEqual({
      catalog: '.venv-data/bin/python data/python/catalog.py',
      quality: '.venv-data/bin/python data/python/quality.py',
      gate: '.venv-data/bin/python data/python/gate.py',
    });
  });
});

describe('the derived data', () => {
  it('loads the four derived files and the four exported contracts', () => {
    expect(Object.keys(data.catalog)).toHaveLength(12);
    expect(data.lineage.edges).toHaveLength(31);
    expect(data.verdicts.as_of).toBe('2026-10-28');
    expect(Object.keys(data.verdicts.datasets)).toEqual([
      'agent-traces/desk-hand', 'agent-traces/desk-hand-go', 'agent-traces/desk-langgraph', 'agent-traces/desk-sdk', 'agent-traces/patterns',
      'agent-traces/patterns-go', 'complaints', 'tickets', 'tickets-v2', 'tickets-v2-before',
    ]);
    expect(data.gate.consumers).toHaveLength(4);
    expect(Object.keys(data.contracts).sort()).toEqual(['agent-traces.v1', 'complaints.v1', 'tickets.v1', 'tickets.v2']);
  });
});

// ── CatalogExplorer ─────────────────────────────────────────────────────────
describe('catalogue explorer data', () => {
  const all = catalogExplorerProps(data.catalog);

  it('shows every dataset by default, in the catalogue order', () => {
    expect(all.datasets.map((d) => d.id)).toEqual([
      'agents_derived', 'agents_traces', 'category_map', 'complaints', 'mcp_derived', 'mcp_recordings', 'ml_registry', 'ml_reports',
      'model_responses', 'reply_labels', 'tickets', 'tickets_v2',
    ]);
    expect(all.initial).toBe('agents_derived');
  });

  it('carries each dataset as catalog.json has it', () => {
    const c = all.datasets.find((d) => d.id === 'complaints')!;
    expect(c).toEqual({
      id: 'complaints',
      description: 'The 3600 public consumer complaints the ML classifier trains on.',
      path: 'ml/data/complaints.jsonl',
      files: 1,
      rows: 3600,
      bytes: 4609144,
      producer: 'ml',
      consumers: ['mcp'],
      contract: 'complaints.v1',
      lastCommit: '54c2346',
      missing: false,
      schema: [['date', 'string'], ['id', 'string'], ['narrative', 'string'], ['product', 'string'], ['product_raw', 'string']],
    });
  });

  it('says each dataset in one sentence', () => {
    const say = (id: string) => datasetSummary(all.datasets.find((d) => d.id === id)!);
    expect(say('complaints')).toBe('complaints: 3,600 rows in 1 file, written by ml, read by mcp, under contract complaints.v1.');
    expect(say('agents_traces')).toBe('agents_traces: 134 rows in 134 files, written by agents, read by agents, under contract agent-traces.v1.');
    expect(say('category_map')).toBe('category_map: 1 row in 1 file, written by ml, read by app and ml, no contract.');
    expect(say('tickets_v2')).toBe('tickets_v2: 60 rows in 1 file, written by data, read by agents, under contract tickets.v2.');
    expect(say('model_responses')).toBe('model_responses: 743 rows in 14 files, written by llms, read by app and llms, no contract.');
  });

  it('narrows to the datasets a lesson names, and refuses one the catalogue does not have', () => {
    const two = catalogExplorerProps(data.catalog, { datasets: ['tickets', 'tickets_v2'] });
    expect(two.datasets.map((d) => d.id)).toEqual(['tickets', 'tickets_v2']);
    expect(two.initial).toBe('tickets');
    expect(catalogExplorerProps(data.catalog, { initial: 'complaints' }).initial).toBe('complaints');
    expect(() => catalogExplorerProps(data.catalog, { datasets: ['nope'] })).toThrow(/CatalogExplorer: unknown dataset "nope"/);
    expect(() => catalogExplorerProps(data.catalog, { datasets: ['tickets'], initial: 'complaints' })).toThrow(/initial "complaints" is not one of/);
  });
});

describe('CatalogExplorer', () => {
  it('shows the picked dataset: path, files, rows, size, producer, consumers, contract, last commit and schema', () => {
    render(<CatalogExplorer {...catalogExplorerProps(data.catalog, { initial: 'tickets' })} />);
    expect(live()).toBe('tickets: 60 rows in 1 file, written by llms, read by mcp, under contract tickets.v1.');
    const facts = screen.getByRole('list', { name: 'About tickets' });
    for (const text of ['llms/data/tickets.jsonl', '16,705 bytes', '19296f8', 'tickets.v1']) expect(facts.textContent).toContain(text);
    const schema = screen.getByRole('table', { name: 'Schema sketch of tickets' });
    expect(within(schema).getAllByRole('row').slice(1).map((r) => r.textContent)).toEqual([
      'categorystring', 'idstring', 'prioritystring', 'splitstring', 'tagsarray', 'textstring',
    ]);
  });

  it('switches dataset from the picker and announces it', () => {
    render(<CatalogExplorer {...catalogExplorerProps(data.catalog)} />);
    fireEvent.change(select('Dataset'), { target: { value: 'mcp_recordings' } });
    expect(live()).toBe('mcp_recordings: 16 rows in 8 files, written by mcp, read by agents and mcp, no contract.');
    expect(screen.getByRole('list', { name: 'About mcp_recordings' }).textContent).toContain('d24b89b');
  });

  it('lists every shown dataset in a table that scrolls in its own box', () => {
    render(<CatalogExplorer {...catalogExplorerProps(data.catalog)} />);
    const table = screen.getByRole('table', { name: 'The catalogue' });
    expect(table.closest('[role="region"]')?.getAttribute('tabindex')).toBe('0');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(12);
    expect(rows[3].textContent).toBe('complaintsml3,600complaints.v1');
    expect(rows[2].textContent).toBe('category_mapml1none');
  });
});

// ── QualityReport ───────────────────────────────────────────────────────────
describe('quality report data', () => {
  it('takes one dataset from verdicts.json, with the pinned as_of', () => {
    const p = qualityReportProps(data.verdicts, { dataset: 'tickets' });
    expect(p.asOf).toBe('2026-10-28');
    expect(p.grouped).toBe(false);
    expect(p.entries).toHaveLength(1);
    expect(p.entries[0]).toMatchObject({ key: 'tickets', contract: 'tickets.v1', path: 'llms/data/tickets.jsonl', files: null, age: 2, ok: true, expectFail: false });
    expect(p.entries[0].verdicts).toHaveLength(21);
  });

  it('counts and says each entry', () => {
    const one = (k: string) => qualityReportProps(data.verdicts, { dataset: k }).entries[0];
    expect(tally(one('tickets').verdicts)).toEqual({ ok: 21, failed: 0 });
    expect(entrySummary(one('tickets'))).toBe('tickets against tickets.v1: 21 verdicts, 21 OK, 0 FAIL.');
    expect(entrySummary(one('complaints'))).toBe('complaints against complaints.v1: 17 verdicts, 17 OK, 0 FAIL.');
    expect(entrySummary(one('tickets-v2'))).toBe('tickets-v2 against tickets.v2: 20 verdicts, 20 OK, 0 FAIL.');
    expect(entrySummary(one('tickets-v2-before'))).toBe('tickets-v2-before against tickets.v2: 21 verdicts, 19 OK, 2 FAIL.');
    expect(entryStatus(one('tickets'))).toBe('PASS');
    expect(entryStatus(one('tickets-v2-before'))).toBe('FAIL, as expected');
    expect(one('complaints').age).toBe(1.8);
  });

  it('groups the six trace sets under agent-traces', () => {
    const p = qualityReportProps(data.verdicts, { dataset: 'agent-traces' });
    expect(p.grouped).toBe(true);
    expect(p.entries.map((e) => [e.label, e.files, e.verdicts.length, e.age, e.ok])).toEqual([
      ['desk-hand', 26, 26, 0, true],
      ['desk-hand-go', 26, 26, 0, true],
      ['desk-langgraph', 26, 26, 0, true],
      ['desk-sdk', 26, 26, 0, true],
      ['patterns', 15, 26, 0, true],
      ['patterns-go', 15, 26, 0, true],
    ]);
  });

  it('refuses a dataset verdicts.json does not have', () => {
    expect(() => qualityReportProps(data.verdicts, { dataset: 'nope' })).toThrow(/QualityReport: no verdicts for "nope" \(known: agent-traces, complaints, tickets, tickets-v2, tickets-v2-before\)/);
  });
});

describe('QualityReport', () => {
  it('lists the verdicts of one dataset with OK and FAIL in words', () => {
    render(<QualityReport {...qualityReportProps(data.verdicts, { dataset: 'tickets-v2-before' })} />);
    expect(screen.getByRole('status').textContent).toContain('FAIL, as expected');
    expect(live()).toBe('tickets-v2-before against tickets.v2: 21 verdicts, 19 OK, 2 FAIL. Showing all 21.');
    const table = screen.getByRole('table', { name: 'Verdicts for tickets-v2-before' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(21);
    expect(rows[3].textContent).toBe('✗ FAILenum_coverageurgencymissing values: "low", "normal", "high", "urgent"');
    expect(rows[0].textContent).toBe('✓ OKenumcategoryall values in enum');
    expect(screen.getByText(/as of 2026-10-28/)).toBeTruthy();
  });

  it('shows only the failures at the press of a button, and says so', () => {
    render(<QualityReport {...qualityReportProps(data.verdicts, { dataset: 'tickets-v2-before' })} />);
    const btn = screen.getByRole('button', { name: 'Show failures only' });
    expect(btn.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(btn);
    expect(btn.getAttribute('aria-pressed')).toBe('true');
    expect(live()).toBe('tickets-v2-before against tickets.v2: 21 verdicts, 19 OK, 2 FAIL. Showing the 2 that failed.');
    const rows = within(screen.getByRole('table', { name: 'Verdicts for tickets-v2-before' })).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      '✗ FAILenum_coverageurgencymissing values: "low", "normal", "high", "urgent"',
      '✗ FAILrequiredurgencyrow 1: missing (+59 more)',
    ]);
  });

  it('says so when a passing dataset has no failures to show', () => {
    render(<QualityReport {...qualityReportProps(data.verdicts, { dataset: 'complaints' })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Show failures only' }));
    expect(live()).toBe('complaints against complaints.v1: 17 verdicts, 17 OK, 0 FAIL. Showing the 0 that failed.');
    expect(screen.getByText('No verdict failed.')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('PASS');
    expect(screen.getByText(/1\.8 days old/)).toBeTruthy();
  });

  it('shows the trace sets as a per-set table, and one set’s verdicts from a picker', () => {
    render(<QualityReport {...qualityReportProps(data.verdicts, { dataset: 'agent-traces' })} />);
    const sets = screen.getByRole('table', { name: 'Trace sets' });
    const rows = within(sets).getAllByRole('row').slice(1).map((r) => r.textContent);
    expect(rows).toEqual([
      'desk-hand26262600 days✓ PASS',
      'desk-hand-go26262600 days✓ PASS',
      'desk-langgraph26262600 days✓ PASS',
      'desk-sdk26262600 days✓ PASS',
      'patterns15262600 days✓ PASS',
      'patterns-go15262600 days✓ PASS',
    ]);
    expect(live()).toBe('desk-hand against agent-traces.v1: 26 verdicts, 26 OK, 0 FAIL. Showing all 26.');
    fireEvent.change(select('Trace set'), { target: { value: 'agent-traces/patterns' } });
    expect(live()).toBe('patterns against agent-traces.v1: 26 verdicts, 26 OK, 0 FAIL. Showing all 26.');
    const first = within(screen.getByRole('table', { name: 'Verdicts for patterns' })).getAllByRole('row')[1];
    expect(first.textContent).toBe('✓ OKenumengine15 files: all values in enum');
  });
});
