import type { ComponentChild } from 'preact';
import { useId, useRef, useState } from 'preact/hooks';
import { type CacheReportProps, type CacheRow } from '../../lib/infraAids';
import InfraMark from './InfraMark';
import { pct, rowMove } from './infraFormat';
import { INFRA_CSS } from './infraStyle';

const HIT = { icon: '✓', word: 'hit', tone: 'ok' } as const;
const rate = (r: CacheRow) => `${pct(r.hit_rate)} (${r.hit_rate})`;
const where = (r: CacheRow) => (r.kind === 'all' ? r.id : `${r.id}, ${r.kind}`);

/** The live region's sentence for one row. */
const rowText = (r: CacheRow) =>
  `${where(r)}: ${r.calls} calls, ${r.hits} hits, hit rate ${rate(r)}; prompt cache ${r.prompt_cache_read_tokens} read and ${r.prompt_cache_write_tokens} write tokens.`;

export default function CacheReport(p: CacheReportProps) {
  const rows = [p.all, ...p.sets, ...p.teams];
  const [pos, setPos] = useState(Math.max(0, rows.findIndex((r) => r.id === p.initial)));
  const refs = useRef<(HTMLTableRowElement | null)[]>([]);
  const selectId = useId();
  const r = rows[pos];

  const onKey = (e: KeyboardEvent) => {
    const next = rowMove(pos, e.key, rows.length);
    if (next === null) return;
    e.preventDefault();
    setPos(next);
    refs.current[next]?.focus();
  };
  // A set and a team may share an id (patterns, judge): the select's values carry the kind.
  const value = (x: CacheRow) => (x.kind === 'team' ? `team:${x.id}` : x.id);
  const onPick = (v: string) => setPos(Math.max(0, rows.findIndex((x) => value(x) === v)));

  const facts: [string, ComponentChild][] = [
    ['calls', String(r.calls)],
    ['hits', <InfraMark mark={{ ...HIT, word: `hit ${r.hits}` }} />],
    ['miss or skip', <span class="dt-mark in-mark-info">○ miss or – skip {r.calls - r.hits}</span>],
    ['hit rate', rate(r)],
    ['prompt-cache read tokens', String(r.prompt_cache_read_tokens)],
    ['prompt-cache write tokens', String(r.prompt_cache_write_tokens)],
  ];

  return (
    <section class="mc" aria-label="Cache report">
      <header class="mc-head">
        <p class="mc-eyebrow">The response cache · {p.all.calls} replayed calls</p>
        <p class="mc-label">Read from capstone.json: no calls from this page</p>
      </header>

      <div class="ag-pick">
        <label for={selectId}>Rows</label>
        <select id={selectId} value={value(r)} onChange={(e) => onPick((e.currentTarget as HTMLSelectElement).value)}>
          <option value={p.all.id}>{p.all.id}</option>
          <optgroup label="Sets">
            {p.sets.map((x) => (
              <option key={x.id} value={value(x)}>
                {x.id}
              </option>
            ))}
          </optgroup>
          <optgroup label="Teams">
            {p.teams.map((x) => (
              <option key={x.id} value={value(x)}>
                {x.id}
              </option>
            ))}
          </optgroup>
        </select>
      </div>
      <ul class="dt-facts" aria-label="Picked row">
        {facts.map(([k, v]) => (
          <li key={k}>
            <span class="dt-key">{k}</span>
            <span class="dt-val">{v}</span>
          </li>
        ))}
      </ul>
      <p class="mc-live" role="status" aria-live="polite">
        {rowText(r)}
      </p>

      <div class="mc-scroll mc-focus" role="region" aria-label="Cache figures table" tabIndex={0}>
        <table class="mc-table in-table in-wide" role="grid" aria-label="Cache figures">
          <thead>
            <tr>
              <th scope="col">Rows</th>
              <th scope="col">Kind</th>
              <th scope="col" class="mc-num">Calls</th>
              <th scope="col">Hits</th>
              <th scope="col">Miss or skip</th>
              <th scope="col" class="mc-num">Hit rate</th>
              <th scope="col" class="mc-num">Prompt-cache read</th>
              <th scope="col" class="mc-num">Prompt-cache write</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x, i) => (
              <tr
                key={`${x.kind} ${x.id}`}
                class="in-row"
                tabIndex={i === pos ? 0 : -1}
                aria-selected={i === pos ? 'true' : 'false'}
                ref={(el) => {
                  refs.current[i] = el;
                }}
                onKeyDown={onKey}
                onFocus={() => setPos(i)}
                onClick={() => setPos(i)}
              >
                <th scope="row">{x.id}</th>
                <td role="gridcell">{x.kind}</td>
                <td role="gridcell" class="mc-num">
                  {x.calls}
                </td>
                <td role="gridcell">
                  <InfraMark mark={{ ...HIT, word: `hit ${x.hits}` }} />
                </td>
                <td role="gridcell">
                  <span class="dt-mark in-mark-info">○ {x.calls - x.hits}</span>
                </td>
                <td role="gridcell" class="mc-num">
                  {rate(x)}
                </td>
                <td role="gridcell" class="mc-num">
                  {x.prompt_cache_read_tokens}
                </td>
                <td role="gridcell" class="mc-num">
                  {x.prompt_cache_write_tokens}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="mc-hint">
        Arrow keys move between the rows and pick one; Home and End jump to the first and the last. ✓ hit: answered from the cache and booked at $0. ○ miss or – skip:
        sent upstream; the figures do not split the two. Prompt-cache tokens are the provider's own cache on the calls that reached the upstream.
      </p>
      <p class="mc-hint">
        ttl_s {p.ttl_s}; skip_tools {String(p.skip_tools)}: {p.skip_tools ? 'a call that carries tools is never cached (skip).' : 'calls with tools are cached too.'}
      </p>
      <p class="in-about">{p.about}</p>
      <style>{INFRA_CSS}</style>
    </section>
  );
}
