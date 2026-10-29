// @vitest-environment jsdom
// The Data Contracts aids: ContractViewer (lessons 4-5) and LineageDiff (lesson 6), plus the track's page budget.
// Every number an aid shows is pinned here as a literal taken from data/contracts/*.contract.json (contract.py
// --export writes them) and data/data/derived/{verdicts,lineage,gate}.json (quality.py, catalog.py, gate.py).
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
// Astro's own prop serialiser and escaper: the budget measures the props as the built page carries them.
import { serializeProps } from 'astro/runtime/server/serialize.js';
import { escapeHTML } from 'astro/runtime/server/escape.js';
import { loadDataData } from '../src/lib/dataData';
import {
  SECTIONS,
  catalogExplorerProps,
  consumerText,
  contractViewerProps,
  fieldRule,
  gateHeadline,
  lineageDiffProps,
  qualityLines,
  qualityReportProps,
  sectionSummary,
  verdictsFrom,
} from '../src/lib/dataAids';
import ContractViewer from '../src/components/data/ContractViewer';
import LineageDiff from '../src/components/data/LineageDiff';

const data = loadDataData();
const live = () => document.querySelector('[aria-live="polite"]')?.textContent;
const select = (label: string) => screen.getByLabelText(label) as HTMLSelectElement;

afterEach(() => cleanup());

// ── ContractViewer ──────────────────────────────────────────────────────────
describe('contract viewer data', () => {
  it('has the five sections, in order', () => {
    expect(SECTIONS).toEqual(['info', 'schema', 'quality', 'servicing', 'consumers']);
  });

  it('passes the contract as its export has it, and the verdicts only when asked', () => {
    const p = contractViewerProps(data.contracts, data.verdicts, { contract: 'tickets.v1' });
    expect(p.contract).toEqual(data.contracts['tickets.v1']);
    expect(p.checks).toEqual([]);
    const v = contractViewerProps(data.contracts, data.verdicts, { contract: 'tickets.v1', verdicts: true });
    expect(v.checks.map((c) => c.key)).toEqual(['tickets']);
    expect(contractViewerProps(data.contracts, data.verdicts, { contract: 'tickets.v2', verdicts: true }).checks.map((c) => c.key)).toEqual(['tickets-v2', 'tickets-v2-before']);
    expect(contractViewerProps(data.contracts, data.verdicts, { contract: 'agent-traces.v1', verdicts: true }).checks).toHaveLength(6);
    expect(() => contractViewerProps(data.contracts, data.verdicts, { contract: 'tickets.v3' })).toThrow(
      /ContractViewer: no contract "tickets.v3" \(known: agent-traces.v1, complaints.v1, tickets.v1, tickets.v2\)/,
    );
  });

  it('says each section of tickets.v1 in one sentence', () => {
    const c = data.contracts['tickets.v1'];
    expect([0, 1, 2, 3, 4].map((i) => sectionSummary(c, i))).toEqual([
      'Section 1 of 5: info. tickets.v1, version 1, deprecated, sunset 2027-01-31.',
      'Section 2 of 5: schema. 6 fields, 6 required.',
      'Section 3 of 5: quality. 3 rules: enum_coverage, row_count, unique.',
      'Section 4 of 5: servicing. Freshness: at most 365 days old.',
      'Section 5 of 5: consumers. 1 consumer: mcp-read-server on version 1.',
    ]);
    expect(sectionSummary(data.contracts['tickets.v2'], 0)).toBe('Section 1 of 5: info. tickets.v2, version 2, active.');
    expect(sectionSummary(data.contracts['complaints.v1'], 1)).toBe('Section 2 of 5: schema. 5 fields, 5 required.');
    expect(sectionSummary(data.contracts['agent-traces.v1'], 2)).toBe('Section 3 of 5: quality. 1 rule: row_count.');
  });

  it("states each field's rule and each quality rule's value", () => {
    const f = Object.fromEntries(data.contracts['tickets.v1'].schema.fields.map((x) => [x.name, fieldRule(x)]));
    expect(f).toEqual({
      id: 'pattern ^t\\d{3}$',
      text: '—',
      category: 'enum: billing, bug, account, feature_request, how_to, outage',
      priority: 'enum: low, normal, high, urgent',
      tags: '—',
      split: 'enum: dev, test',
    });
    expect(qualityLines(data.contracts['tickets.v1'].quality)).toEqual([
      ['enum_coverage', 'category, priority'],
      ['row_count', 'min 60, max 60'],
      ['unique', 'id'],
    ]);
    expect(qualityLines(data.contracts['complaints.v1'].quality)).toContainEqual(['row_count', 'min 3600, max 3600']);
  });

  it('sorts the verdicts by the section that declares their rule', () => {
    const e = contractViewerProps(data.contracts, data.verdicts, { contract: 'tickets.v1', verdicts: true }).checks[0];
    expect(SECTIONS.map((s) => verdictsFrom(e, s).length)).toEqual([0, 16, 4, 1, 0]);
  });
});

describe('ContractViewer', () => {
  it('steps through the five sections by button and by arrow key, announcing each', () => {
    render(<ContractViewer {...contractViewerProps(data.contracts, data.verdicts, { contract: 'tickets.v1' })} />);
    expect(live()).toBe('Section 1 of 5: info. tickets.v1, version 1, deprecated, sunset 2027-01-31.');
    expect(screen.getByRole('table', { name: 'info of tickets.v1' }).textContent).toContain('2027-01-31');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(live()).toBe('Section 2 of 5: schema. 6 fields, 6 required.');
    const fields = within(screen.getByRole('table', { name: 'schema of tickets.v1' })).getAllByRole('row').slice(1);
    expect(fields[0].textContent).toBe('idstringyespattern ^t\\d{3}$the ticket\'s stable key, t001 to t060; consumers look tickets up by it');
    fireEvent.keyDown(screen.getByRole('group', { name: 'Sections' }), { key: 'ArrowRight' });
    expect(live()).toBe('Section 3 of 5: quality. 3 rules: enum_coverage, row_count, unique.');
    fireEvent.keyDown(screen.getByRole('group', { name: 'Sections' }), { key: 'ArrowRight' });
    fireEvent.keyDown(screen.getByRole('group', { name: 'Sections' }), { key: 'ArrowRight' });
    expect(live()).toBe('Section 5 of 5: consumers. 1 consumer: mcp-read-server on version 1.');
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(screen.getByRole('table', { name: 'servicing of tickets.v1' }).textContent).toBe('PromiseValuefreshnessat most 365 days old');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByRole('table', { name: 'consumers of tickets.v1' }).textContent).toContain('llms/data/tickets.jsonl via mcp/core/python/lumen_core/tickets.py');
    expect(screen.queryByRole('table', { name: /^Verdicts/ })).toBeNull();
  });

  it('puts the verdicts each section produced beside it when asked', () => {
    render(<ContractViewer {...contractViewerProps(data.contracts, data.verdicts, { contract: 'tickets.v1', verdicts: true })} />);
    expect(live()).toBe('Section 1 of 5: info. tickets.v1, version 1, deprecated, sunset 2027-01-31. No verdict comes from this section.');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(live()).toBe('Section 2 of 5: schema. 6 fields, 6 required. 16 of the 21 verdicts on tickets come from this section.');
    const rows = within(screen.getByRole('table', { name: 'Verdicts on tickets from schema' })).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(16);
    expect(rows[0].textContent).toBe('✓ OKenumcategoryall values in enum');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(live()).toBe('Section 4 of 5: servicing. Freshness: at most 365 days old. 1 of the 21 verdicts on tickets comes from this section.');
  });

  it('picks which checked file the verdicts are from when a contract governs more than one', () => {
    render(<ContractViewer {...contractViewerProps(data.contracts, data.verdicts, { contract: 'tickets.v2', verdicts: true })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    fireEvent.change(select('Checked file'), { target: { value: 'tickets-v2-before' } });
    expect(live()).toBe('Section 2 of 5: schema. 6 fields, 6 required. 17 of the 21 verdicts on tickets-v2-before come from this section.');
    const fails = within(screen.getByRole('table', { name: 'Verdicts on tickets-v2-before from schema' }))
      .getAllByRole('row')
      .slice(1)
      .filter((r) => r.textContent!.startsWith('✗ FAIL'));
    expect(fails.map((r) => r.textContent)).toEqual(['✗ FAILrequiredurgencyrow 1: missing (+59 more)']);
  });
});

// ── LineageDiff ─────────────────────────────────────────────────────────────
describe('lineage diff data', () => {
  const p = lineageDiffProps(data, { from: 'tickets.v1', to: 'tickets.v2' });

  it('puts the two versions side by side, with their datasets and consumers', () => {
    expect(p.from).toEqual({
      id: 'tickets.v1', version: '1', status: 'deprecated', sunset: '2027-01-31', path: 'llms/data/tickets.jsonl', dataset: 'tickets',
      consumers: [{ name: 'mcp-read-server', reads: 'llms/data/tickets.jsonl via mcp/core/python/lumen_core/tickets.py' }],
    });
    expect(p.to).toEqual({
      id: 'tickets.v2', version: '2', status: 'active', sunset: null, path: 'data/data/tickets.v2.jsonl', dataset: 'tickets_v2',
      consumers: [{ name: 'agents-eval', reads: 'data/data/tickets.v2.jsonl via data/python/consumers/agents_eval_v2.py' }],
    });
    expect(p.asOf).toBe('2026-10-28');
  });

  it("draws the migration edge from lineage.json and quotes v2's migration note", () => {
    expect(p.migration).toEqual({ from: 'tickets', to: 'tickets_v2', contract: 'tickets.v1', version: '1' });
    expect(p.migrationNote).toBe(data.contracts['tickets.v2'].info.migration);
    expect(p.migrationNote).toContain('priority is renamed urgency');
  });

  it('lists what changed between the two schemas', () => {
    expect(p.fields).toEqual([
      { field: 'category', change: 'enum gains security' },
      { field: 'priority', change: 'only in tickets.v1' },
      { field: 'urgency', change: 'only in tickets.v2' },
    ]);
  });

  it("carries the gate's rows for both versions and its verdict verbatim", () => {
    expect(p.gate).toEqual({ ok: true, reason: 'every consumer is green on the version it declares (4 of 4)' });
    expect(gateHeadline(p.gate)).toBe('Gate: green');
    expect(gateHeadline({ ok: false, reason: 'x' })).toBe('Gate: red');
    expect(p.consumers).toEqual([
      { name: 'mcp-read-server', declared: 'tickets.v1', green: true, sunset: '2027-01-31', warning: false },
      { name: 'agents-eval', declared: 'tickets.v2', green: true, sunset: null, warning: false },
    ]);
    expect(consumerText(p.consumers[0], p.asOf)).toBe(
      'mcp-read-server reads tickets.v1: OK. Sunset 2027-01-31; as of 2026-10-28 that is still ahead, so no warning yet.',
    );
    expect(consumerText(p.consumers[1], p.asOf)).toBe('agents-eval reads tickets.v2: OK.');
    expect(consumerText({ name: 'x', declared: 'tickets.v1', green: false, sunset: '2027-01-31', warning: true }, '2027-02-01')).toBe(
      'x reads tickets.v1: FAIL. Past its sunset 2027-01-31: a warning, not a failure.',
    );
    // The sunset is compared with as_of, not inferred from the missing warning.
    expect(consumerText({ name: 'x', declared: 'tickets.v1', green: true, sunset: '2027-01-31', warning: false }, '2027-02-01')).toBe(
      'x reads tickets.v1: OK. Sunset 2027-01-31 has passed as of 2027-02-01, but gate.json has no warning: rerun gate.py.',
    );
  });

  it('refuses two versions of different contracts, or a version that does not exist', () => {
    expect(() => lineageDiffProps(data, { from: 'tickets.v1', to: 'complaints.v1' })).toThrow(/LineageDiff: tickets.v1 and complaints.v1 are not versions of one contract/);
    expect(() => lineageDiffProps(data, { from: 'tickets.v1', to: 'tickets.v3' })).toThrow(/LineageDiff: no contract "tickets.v3"/);
  });
});

describe('LineageDiff', () => {
  it('shows each version, the migration edge, the field changes and the gate verdict as written', () => {
    render(<LineageDiff {...lineageDiffProps(data, { from: 'tickets.v1', to: 'tickets.v2' })} />);
    const v1 = screen.getByRole('region', { name: 'tickets.v1' });
    expect(v1.textContent).toContain('deprecated');
    expect(v1.textContent).toContain('Sunset: 2027-01-31');
    expect(v1.textContent).toContain('✓ OK mcp-read-server');
    const v2 = screen.getByRole('region', { name: 'tickets.v2' });
    expect(v2.textContent).toContain('active');
    expect(v2.textContent).toContain('✓ OK agents-eval');
    expect(screen.getByText('tickets → tickets_v2, reading tickets.v1')).toBeTruthy();
    expect(screen.getByText(/priority is renamed urgency/)).toBeTruthy();
    const changes = within(screen.getByRole('table', { name: 'What changed from tickets.v1 to tickets.v2' })).getAllByRole('row').slice(1);
    expect(changes.map((r) => r.textContent)).toEqual(['categoryenum gains security', 'priorityonly in tickets.v1', 'urgencyonly in tickets.v2']);
    const gate = screen.getByRole('status');
    expect(gate.getAttribute('aria-live')).toBe('off'); // one announcement per change: the live line only
    expect(v1.textContent).toContain('No warning: as of 2026-10-28 the sunset is still ahead.');
    expect(gate.textContent).toContain('Gate: green');
    expect(gate.textContent).toContain('every consumer is green on the version it declares (4 of 4)');
  });

  it('announces the consumer picked', () => {
    render(<LineageDiff {...lineageDiffProps(data, { from: 'tickets.v1', to: 'tickets.v2' })} />);
    expect(live()).toBe('mcp-read-server reads tickets.v1: OK. Sunset 2027-01-31; as of 2026-10-28 that is still ahead, so no warning yet.');
    fireEvent.change(select('Consumer'), { target: { value: 'agents-eval on tickets.v2' } });
    expect(live()).toBe('agents-eval reads tickets.v2: OK.');
  });
});

// ── Page budget (spec §6: the track's props under 200 KB) ───────────────────
// What the lessons plan to ship (spec §5: CatalogExplorer in lesson 2, QualityReport in 3, ContractViewer in 4 and
// with verdicts in 5, LineageDiff in 6), taken at its largest: every dataset in the explorer, every QualityReport
// dataset, every contract with and without verdicts, serialised and escaped by Astro's own code as the built page
// carries them. The built page itself is measured beside the agents budget in agentsaids-evals.test.tsx (one
// build for both), and counts the data lessons once Task 7 adds them.
const shipped = (props: object) => Buffer.byteLength(escapeHTML(serializeProps(props, {} as never)), 'utf8');

describe('page budget', () => {
  it('keeps the largest props the data lessons could ship under 200 KB', () => {
    const parts: [string, number][] = [
      ['CatalogExplorer (all)', shipped(catalogExplorerProps(data.catalog))],
      ...['agent-traces', 'complaints', 'tickets', 'tickets-v2', 'tickets-v2-before'].map((d) => [`QualityReport ${d}`, shipped(qualityReportProps(data.verdicts, { dataset: d }))] as [string, number]),
      ...Object.keys(data.contracts).flatMap((c) => [
        [`ContractViewer ${c}`, shipped(contractViewerProps(data.contracts, data.verdicts, { contract: c }))] as [string, number],
        [`ContractViewer ${c} + verdicts`, shipped(contractViewerProps(data.contracts, data.verdicts, { contract: c, verdicts: true }))] as [string, number],
      ]),
      ['LineageDiff tickets', shipped(lineageDiffProps(data, { from: 'tickets.v1', to: 'tickets.v2' }))],
    ];
    const total = parts.reduce((a, [, n]) => a + n, 0);
    expect(total, parts.map(([k, n]) => `${k} ${n}`).join(', ')).toBeLessThan(200_000);
    // Each aid ships something real, and a contract's own props stay small without its verdicts.
    for (const [k, n] of parts) expect(n, k).toBeGreaterThan(500);
    expect(shipped(contractViewerProps(data.contracts, data.verdicts, { contract: 'tickets.v1' }))).toBeLessThan(5_000);
  });
});
