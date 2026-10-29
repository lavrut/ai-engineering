// Round trips for the data aids' props: the aids narrow the derived JSON to what a lesson shows, but never
// rewrite it. Every entry they can ship is taken back to the record it came from and compared field by field,
// so a narrowing that drops, renames or rounds a value fails here.
// (a) CatalogExplorer: each DatasetView rebuilds its catalog.json entry exactly (the schema sketch's pairs back
//     to an object, in the same key order).
// (b) QualityReport and ContractViewer: each entry rebuilds its verdicts.json entry exactly, every verdict in
//     order, and the verdicts per section add up to the whole list (no verdict belongs to two sections or none).
// (c) LineageDiff: each version, consumer row and the migration edge are found as they are in the contract
//     exports, gate.json and lineage.json.
import { describe, expect, it } from 'vitest';
import { loadDataData } from '../src/lib/dataData';
import {
  SECTIONS,
  catalogExplorerProps,
  contractViewerProps,
  lineageDiffProps,
  qualityReportProps,
  verdictView,
  verdictsFrom,
  type QualityEntryView,
  type VerdictEntryJson,
} from '../src/lib/dataAids';

const data = loadDataData();

describe('CatalogExplorer round trip', () => {
  const shown = catalogExplorerProps(data.catalog).datasets;

  it('covers every catalogued dataset', () => {
    expect(shown).toHaveLength(Object.keys(data.catalog).length);
  });

  it.each(shown.map((d) => [d.id, d] as const))('%s rebuilds its catalog.json entry', (id, d) => {
    const back = {
      bytes: d.bytes,
      consumers: d.consumers,
      contract: d.contract,
      description: d.description,
      files: d.files,
      last_commit: d.lastCommit,
      missing: d.missing,
      path: d.path,
      producer: d.producer,
      rows: d.rows,
      schema: Object.fromEntries(d.schema),
    };
    expect(back).toEqual(data.catalog[id]);
    expect(Object.keys(back.schema)).toEqual(Object.keys(data.catalog[id].schema));
  });
});

const backToJson = (e: QualityEntryView): VerdictEntryJson => ({
  age_days: e.age,
  contract: e.contract,
  ...(e.expectFail ? { expect: 'fail' } : {}),
  ...(e.files === null ? {} : { files: e.files }),
  ok: e.ok,
  path: e.path,
  verdicts: e.verdicts,
});

describe('QualityReport and ContractViewer round trip', () => {
  const fromReports = ['agent-traces', 'complaints', 'tickets', 'tickets-v2', 'tickets-v2-before'].flatMap((d) => qualityReportProps(data.verdicts, { dataset: d }).entries);
  const fromViewers = Object.keys(data.contracts).flatMap((c) => contractViewerProps(data.contracts, data.verdicts, { contract: c, verdicts: true }).checks);

  it('reach every verdicts.json entry, each exactly once per aid', () => {
    const keys = Object.keys(data.verdicts.datasets).sort();
    expect(fromReports.map((e) => e.key).sort()).toEqual(keys);
    expect(fromViewers.map((e) => e.key).sort()).toEqual(keys);
  });

  it.each(fromReports.map((e) => [e.key, e] as const))('QualityReport %s rebuilds its verdicts.json entry', (key, e) => {
    expect(backToJson(e)).toEqual(data.verdicts.datasets[key]);
  });

  it.each(fromViewers.map((e) => [e.key, e] as const))('ContractViewer %s rebuilds its verdicts.json entry, section by section', (key, e) => {
    expect(backToJson(e)).toEqual(data.verdicts.datasets[key]);
    const bySection = SECTIONS.flatMap((s) => verdictsFrom(e, s));
    expect(bySection).toHaveLength(e.verdicts.length);
    expect(new Set(bySection)).toEqual(new Set(e.verdicts));
    for (const v of e.verdicts) {
      const view = verdictView(v);
      expect(view.mark).toBe(v.ok ? 'OK' : 'FAIL');
      expect([view.rule, view.field === '(whole file)' ? null : view.field, view.ok, view.detail]).toEqual([v.rule, v.field, v.ok, v.detail]);
    }
  });

  it("ContractViewer ships each contract export unchanged", () => {
    for (const c of Object.keys(data.contracts)) expect(contractViewerProps(data.contracts, data.verdicts, { contract: c }).contract).toEqual(data.contracts[c]);
  });
});

describe('LineageDiff round trip', () => {
  const p = lineageDiffProps(data, { from: 'tickets.v1', to: 'tickets.v2' });

  it('takes each version from its contract export and the catalogue', () => {
    for (const v of [p.from, p.to]) {
      const c = data.contracts[v.id];
      expect([v.id, v.version, v.status, v.sunset, v.path]).toEqual([c.info.id, c.info.version, c.info.status, c.info.sunset ?? null, c.info.dataset]);
      expect(v.consumers).toEqual(c.consumers.map(({ name, reads }) => ({ name, reads })));
      expect(data.catalog[v.dataset].contract).toBe(v.id);
    }
  });

  it("takes each consumer row from gate.json, and the gate's verdict verbatim", () => {
    for (const r of p.consumers) {
      const g = data.gate.consumers.find((x) => x.name === r.name && x.declared === r.declared)!;
      expect(r).toEqual({ name: g.name, declared: g.declared, green: g.contract_exists && g.verdicts_ok, sunset: g.sunset, warning: g.warning });
    }
    expect(p.consumers).toHaveLength(data.gate.consumers.filter((g) => g.declared === 'tickets.v1' || g.declared === 'tickets.v2').length);
    expect(p.gate).toEqual({ ok: data.gate.ok, reason: data.gate.reason });
  });

  it('takes the migration edge from lineage.json as it is', () => {
    expect(data.lineage.edges).toContainEqual(p.migration);
  });
});
