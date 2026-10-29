// @vitest-environment jsdom
// The Ontology track's browser aid: OntologyBrowser (lesson 2), plus the module rules every ontology aid keeps.
// Every number the aid shows is pinned here as a literal taken from ontology/data/derived/ontology.json
// (.venv-ontology/bin/python ontology/python/ontology.py export writes it from ontology/lumen.yaml).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/preact';
import { loadOntologyData } from '../src/lib/ontologyData';
import {
  ONTOLOGY_COMMANDS,
  cardinalityText,
  classSummary,
  constraintRule,
  neighbour,
  ontologyBrowserProps,
  rangeText,
} from '../src/lib/ontologyAids';
import OntologyBrowser from '../src/components/ontology/OntologyBrowser';

const here = dirname(fileURLToPath(import.meta.url));
const data = loadOntologyData();
const live = () => document.querySelector('[aria-live="polite"]')?.textContent;

afterEach(() => cleanup());

// ── The modules ─────────────────────────────────────────────────────────────
const ONTOLOGY_LIB = ['ontologyAids.ts', 'ontologyAidsTypes.ts', 'ontologyAidsBrowser.ts', 'ontologyAidsGrounding.ts', 'ontologyAidsAudit.ts', 'ontologyData.ts'];
const ISLANDS = ['OntologyBrowser.tsx', 'GroundingReport.tsx', 'AuditTable.tsx'];
const WRAPPERS = ['OntologyBrowser.astro', 'GroundingReport.astro', 'AuditTable.astro'];
const comp = (f: string) => readFileSync(join(here, '../src/components/ontology', f), 'utf8');

describe('ontology aid modules', () => {
  it('are split per aid, each under 400 lines, and ontologyAids.ts only re-exports them', () => {
    for (const f of ONTOLOGY_LIB) expect(readFileSync(join(here, '../src/lib', f), 'utf8').split('\n').length, f).toBeLessThan(400);
    const barrel = readFileSync(join(here, '../src/lib/ontologyAids.ts'), 'utf8');
    expect(barrel.match(/^export \* from '\.\/ontologyAids\w+';$/gm)).toHaveLength(4);
  });

  it('islands import only the browser-safe barrel, never the build-time loader, and each has a live region', () => {
    for (const f of ISLANDS) {
      const src = comp(f);
      expect(src, f).not.toMatch(/ontologyData|codeSourceText|dataData/);
      expect(src, f).toMatch(/from '\.\.\/\.\.\/lib\/ontologyAids'/);
      expect(src, f).toMatch(/aria-live="polite"/);
      expect(src.split('\n').length, f).toBeLessThan(250);
    }
  });

  it('wrappers read the data at build time and hand the island its props', () => {
    for (const f of WRAPPERS) {
      const src = comp(f);
      expect(src, f).toMatch(/loadOntologyData\(\)/);
      expect(src, f).toMatch(/client:visible/);
    }
  });

  it('puts every table in its own scroll box, with a minimum width, so 375 px never scrolls the page; tokens only', () => {
    for (const f of ISLANDS) {
      const src = comp(f);
      const tables = src.match(/<table\b/g) ?? [];
      const boxes = src.match(/<div class="mc-scroll mc-focus" role="region" aria-label="[^"]+" tabIndex=\{0\}>\s*<table\b/g) ?? [];
      expect(boxes.length, f).toBe(tables.length);
    }
    const style = comp('ontologyStyle.ts');
    expect(style).toMatch(/\.on-table \{ min-width: \d+rem; \}/);
    expect(style).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(style).not.toMatch(/rgb\(|hsl\(/);
  });

  it('names the commands that rewrite the derived files', () => {
    expect(ONTOLOGY_COMMANDS).toEqual({
      ontology: '.venv-ontology/bin/python ontology/python/ontology.py export',
      generated: '.venv-ontology/bin/python ontology/python/generate.py',
      validation: '.venv-ontology/bin/python ontology/python/grounding.py',
      audit: '.venv-ontology/bin/python ontology/python/audit.py',
    });
  });
});

describe('the derived data', () => {
  it('loads the four derived files', () => {
    expect(data.ontology.source).toBe('ontology/lumen.yaml');
    expect([data.ontology.classes.length, data.ontology.properties.length, data.ontology.relations.length]).toEqual([7, 6, 5]);
    expect([data.ontology.individuals.length, data.ontology.constraints.length]).toEqual([16, 25]);
    expect(Object.keys(data.generated.compare)).toEqual(['definitions', 'schema', 'tool']);
    expect(data.validation.totals).toEqual({ records: 700, violations: { feature_request_never_above_normal: 7 } });
    expect(Object.keys(data.validation.sources)).toHaveLength(16);
    expect(data.audit.rows).toHaveLength(71);
    expect(data.audit.gate).toEqual({ disagree: 1, missing: 0, ok: false, reason: 'red: 1 disagree (data/contracts/tickets.v2.yaml enum:category); 0 missing' });
  });
});

// ── OntologyBrowser ─────────────────────────────────────────────────────────
describe('ontology browser data', () => {
  const all = ontologyBrowserProps(data.ontology);
  const cls = (id: string) => all.classes.find((c) => c.id === id)!;

  it('shows every class in the file order, the first one by default', () => {
    expect(all.classes.map((c) => c.id)).toEqual(['Category', 'Complaint', 'Customer', 'Priority', 'Product', 'Reply', 'Ticket']);
    expect(all.initial).toBe('Category');
    expect(all.source).toBe('ontology/lumen.yaml');
    expect(ontologyBrowserProps(data.ontology, { cls: 'Ticket' }).initial).toBe('Ticket');
    expect(() => ontologyBrowserProps(data.ontology, { cls: 'Nope' })).toThrow(
      /OntologyBrowser: unknown class "Nope" \(known: Category, Complaint, Customer, Priority, Product, Reply, Ticket\)/,
    );
  });

  it("gathers each class's properties, relations in and out, individuals and constraints", () => {
    const shape = (id: string) => {
      const c = cls(id);
      return [c.properties.length, c.out.map((r) => r.id), c.in.map((r) => r.id), c.individuals.length, c.constraints.length];
    };
    expect(shape('Category')).toEqual([0, [], ['has_category'], 6, 0]);
    expect(shape('Complaint')).toEqual([0, ['about'], [], 0, 0]);
    expect(shape('Customer')).toEqual([0, [], ['raised_by'], 0, 0]);
    expect(shape('Priority')).toEqual([0, [], ['has_priority'], 4, 0]);
    expect(shape('Product')).toEqual([0, [], ['about'], 6, 0]);
    expect(shape('Reply')).toEqual([0, ['answers'], [], 0, 0]);
    expect(shape('Ticket')).toEqual([6, ['has_category', 'has_priority', 'raised_by'], ['answers'], 0, 25]);
  });

  it('orders individuals by the guide decision order', () => {
    expect(cls('Category').individuals.map((i) => `${i.order} ${i.id} ${i.source}`)).toEqual([
      '1 outage C1', '2 billing C2', '3 account C3', '4 feature_request C4', '5 bug C5', '6 how_to C6',
    ]);
    expect(cls('Priority').individuals.map((i) => `${i.order} ${i.id} ${i.source}`)).toEqual(['1 urgent U1-U4', '2 high H1-H4', '3 low L1-L3', '4 normal N']);
    expect(cls('Product').individuals[0]).toMatchObject({ id: 'credit_reporting', order: 1, source: 'category_map', label: 'Credit reporting or other personal consumer reports' });
  });

  it('says a range and a cardinality in words', () => {
    const p = (id: string) => cls('Ticket').properties.find((x) => x.id === id)!;
    expect(rangeText(p('category').range)).toBe('one of Category: outage, billing, account, feature_request, bug, how_to');
    expect(rangeText(p('priority').range)).toBe('one of Priority: urgent, high, low, normal');
    expect(rangeText(p('split').range)).toBe('one of the values: dev, test');
    expect(rangeText(p('id').range)).toBe('string');
    expect(p('id').pattern).toBe('^t\\d{3}$');
    expect(cardinalityText(p('tags').cardinality, p('tags').required)).toBe('many, required');
    expect(cardinalityText(p('text').cardinality, p('text').required)).toBe('exactly one, required');
    expect(cardinalityText('one', null)).toBe('exactly one');
  });

  it('says a checkable constraint as its rule, and a manual one has none', () => {
    const c = (id: string) => cls('Ticket').constraints.find((x) => x.id === id)!;
    expect(constraintRule(c('feature_request_never_above_normal'))).toBe('when category = feature_request, then priority in normal, low');
    expect(constraintRule(c('security'))).toBeNull();
    expect(cls('Ticket').constraints.filter((x) => x.kind === 'checkable').map((x) => x.id)).toEqual(['feature_request_never_above_normal']);
    expect(cls('Ticket').constraints.filter((x) => x.kind === 'manual')).toHaveLength(24);
  });

  it('sums each class up in one sentence', () => {
    expect(classSummary(cls('Ticket'))).toBe('Ticket: 6 properties, 3 relations out, 1 relation in, 0 individuals, 25 constraints (1 checkable, 24 manual).');
    expect(classSummary(cls('Category'))).toBe('Category: 0 properties, 0 relations out, 1 relation in, 6 individuals, 0 constraints.');
    expect(classSummary(cls('Product'))).toBe('Product: 0 properties, 0 relations out, 1 relation in, 6 individuals, 0 constraints.');
  });

  it('moves between classes by arrow keys, Home and End, wrapping at the ends', () => {
    const ids = all.classes.map((c) => c.id);
    expect(neighbour(ids, 'Category', 'ArrowRight')).toBe('Complaint');
    expect(neighbour(ids, 'Category', 'ArrowDown')).toBe('Complaint');
    expect(neighbour(ids, 'Category', 'ArrowLeft')).toBe('Ticket');
    expect(neighbour(ids, 'Ticket', 'ArrowRight')).toBe('Category');
    expect(neighbour(ids, 'Reply', 'ArrowUp')).toBe('Product');
    expect(neighbour(ids, 'Reply', 'Home')).toBe('Category');
    expect(neighbour(ids, 'Category', 'End')).toBe('Ticket');
    expect(neighbour(ids, 'Category', 'Enter')).toBeNull();
  });
});

describe('OntologyBrowser', () => {
  it('shows the first class by default: its definition, relation in and individuals with order and definition', () => {
    render(<OntologyBrowser {...ontologyBrowserProps(data.ontology)} />);
    expect(live()).toBe('Category: 0 properties, 0 relations out, 1 relation in, 6 individuals, 0 constraints.');
    expect(screen.getByRole('tab', { name: 'Category' }).getAttribute('aria-selected')).toBe('true');
    const panel = screen.getByRole('tabpanel');
    expect(panel.textContent).toContain('What a ticket is about. Exactly one per ticket');
    const inds = within(screen.getByRole('table', { name: 'Individuals of Category' })).getAllByRole('row').slice(1);
    expect(inds).toHaveLength(6);
    expect(inds[0].textContent).toMatch(/^1outageC1The service, or a whole feature/);
    const rel = within(screen.getByRole('table', { name: 'Relations of Category' })).getAllByRole('row').slice(1);
    expect(rel.map((r) => r.textContent)).toEqual(['inhas_categoryTicket → Categoryexactly oneA ticket has exactly one category.']);
  });

  it('shows Ticket: properties with range and cardinality, relations out and in, and constraints with their kind', () => {
    render(<OntologyBrowser {...ontologyBrowserProps(data.ontology, { cls: 'Ticket' })} />);
    expect(live()).toBe('Ticket: 6 properties, 3 relations out, 1 relation in, 0 individuals, 25 constraints (1 checkable, 24 manual).');
    const props = within(screen.getByRole('table', { name: 'Properties of Ticket' })).getAllByRole('row').slice(1);
    expect(props.map((r) => r.textContent)).toEqual([
      'categoryone of Category: outage, billing, account, feature_request, bug, how_toexactly one, required',
      'idstring, pattern ^t\\d{3}$exactly one, required',
      'priorityone of Priority: urgent, high, low, normalexactly one, required',
      'splitone of the values: dev, testexactly one, required',
      'tagsstringmany, required',
      'textstringexactly one, required',
    ]);
    const rel = within(screen.getByRole('table', { name: 'Relations of Ticket' })).getAllByRole('row').slice(1);
    expect(rel.map((r) => r.textContent!.split('Ticket')[0])).toEqual(['outhas_category', 'outhas_priority', 'outraised_by', 'inanswersReply → ']);
    const cons = within(screen.getByRole('table', { name: 'Constraints on Ticket' })).getAllByRole('row').slice(1);
    expect(cons).toHaveLength(25);
    const checkable = cons.find((r) => r.textContent!.includes('feature_request_never_above_normal'))!;
    expect(checkable.textContent).toContain('checkable');
    expect(checkable.textContent).toContain('when category = feature_request, then priority in normal, low');
    expect(cons.filter((r) => /^[a-z_0-9]+manual/.test(r.textContent!))).toHaveLength(24);
    expect(screen.queryByRole('table', { name: 'Individuals of Ticket' })).toBeNull();
    expect(screen.getByRole('tabpanel').textContent).toContain('No individuals');
  });

  it('moves between classes with the arrow keys and announces the new class', () => {
    render(<OntologyBrowser {...ontologyBrowserProps(data.ontology)} />);
    const first = screen.getByRole('tab', { name: 'Category' });
    expect(first.getAttribute('tabindex')).toBe('0');
    expect(screen.getByRole('tab', { name: 'Ticket' }).getAttribute('tabindex')).toBe('-1');
    fireEvent.keyDown(first, { key: 'ArrowLeft' });
    expect(screen.getByRole('tab', { name: 'Ticket' }).getAttribute('aria-selected')).toBe('true');
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Ticket' }));
    expect(live()).toMatch(/^Ticket: 6 properties/);
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Ticket' }), { key: 'Home' });
    expect(live()).toMatch(/^Category:/);
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Category' }), { key: 'ArrowRight' });
    expect(live()).toBe('Complaint: 0 properties, 1 relation out, 0 relations in, 0 individuals, 0 constraints.');
    fireEvent.click(screen.getByRole('tab', { name: 'Product' }));
    expect(live()).toBe('Product: 0 properties, 0 relations out, 1 relation in, 6 individuals, 0 constraints.');
  });
});
