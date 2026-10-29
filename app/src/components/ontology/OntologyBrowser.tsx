import { useId, useRef, useState } from 'preact/hooks';
import { cardinalityText, classSummary, constraintRule, neighbour, rangeText, type OntologyBrowserProps } from '../../lib/ontologyAids';
import { ONTOLOGY_CSS } from './ontologyStyle';

export default function OntologyBrowser({ source, classes, initial }: OntologyBrowserProps) {
  const [id, setId] = useState(initial);
  const tabs = useRef<Record<string, HTMLButtonElement | null>>({});
  const panelId = useId();
  const c = classes.find((x) => x.id === id) ?? classes[0];
  const ids = classes.map((x) => x.id);
  const relations = [...c.out.map((r) => ['out', r] as const), ...c.in.map((r) => ['in', r] as const)];

  const onKey = (e: KeyboardEvent) => {
    const next = neighbour(ids, c.id, e.key);
    if (next === null) return;
    e.preventDefault();
    setId(next);
    tabs.current[next]?.focus();
  };

  return (
    <section class="mc" aria-label="Ontology browser">
      <header class="mc-head">
        <p class="mc-eyebrow">Lumen's ontology · {classes.length} classes</p>
        <p class="mc-label">Read from ontology.json ({source}): no calls from this page</p>
      </header>

      <div class="on-tabs" role="tablist" aria-label="Classes">
        {classes.map((x) => (
          <button
            key={x.id}
            type="button"
            role="tab"
            class="on-tab"
            id={`${panelId}-${x.id}`}
            aria-selected={x.id === c.id ? 'true' : 'false'}
            aria-controls={panelId}
            tabIndex={x.id === c.id ? 0 : -1}
            ref={(el) => {
              tabs.current[x.id] = el;
            }}
            onClick={() => setId(x.id)}
            onKeyDown={onKey}
          >
            {x.label}
          </button>
        ))}
      </div>
      <p class="mc-hint">Arrow keys move between the classes; Home and End jump to the first and the last.</p>

      <p class="mc-live" aria-live="polite">
        {classSummary(c)}
      </p>

      <div class="on-panel" role="tabpanel" id={panelId} aria-labelledby={`${panelId}-${c.id}`}>
        <p class="on-def">{c.definition}</p>

        <h3 class="mc-h">Properties</h3>
        {c.properties.length === 0 ? (
          <p class="mc-hint">No properties: a {c.id} is described by its relations and individuals.</p>
        ) : (
          <div class="mc-scroll mc-focus" role="region" aria-label="Properties table" tabIndex={0}>
            <table class="mc-table on-table" aria-label={`Properties of ${c.id}`}>
              <thead>
                <tr>
                  <th scope="col">Property</th>
                  <th scope="col">Range</th>
                  <th scope="col">Cardinality</th>
                </tr>
              </thead>
              <tbody>
                {c.properties.map((p) => (
                  <tr key={p.id}>
                    <th scope="row">{p.id}</th>
                    <td>{p.pattern === null ? rangeText(p.range) : `${rangeText(p.range)}, pattern ${p.pattern}`}</td>
                    <td>{cardinalityText(p.cardinality, p.required)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h3 class="mc-h">Relations</h3>
        {relations.length === 0 ? (
          <p class="mc-hint">No relation names {c.id}.</p>
        ) : (
          <div class="mc-scroll mc-focus" role="region" aria-label="Relations table" tabIndex={0}>
            <table class="mc-table on-table" aria-label={`Relations of ${c.id}`}>
              <thead>
                <tr>
                  <th scope="col">Direction</th>
                  <th scope="col">Relation</th>
                  <th scope="col">From → to</th>
                  <th scope="col">Cardinality</th>
                  <th scope="col">Meaning</th>
                </tr>
              </thead>
              <tbody>
                {relations.map(([dir, r]) => (
                  <tr key={`${dir} ${r.id}`}>
                    <td>{dir}</td>
                    <th scope="row">{r.id}</th>
                    <td>
                      {r.domain} → {r.range}
                    </td>
                    <td>{cardinalityText(r.cardinality, null)}</td>
                    <td>{r.label}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h3 class="mc-h">Individuals</h3>
        {c.individuals.length === 0 ? (
          <p class="mc-hint">No individuals: the ontology names no members of {c.id}; the records are the members.</p>
        ) : (
          <div class="mc-scroll mc-focus" role="region" aria-label="Individuals table" tabIndex={0}>
            <table class="mc-table on-table" aria-label={`Individuals of ${c.id}`}>
              <thead>
                <tr>
                  <th scope="col" class="mc-num">
                    Order
                  </th>
                  <th scope="col">Individual</th>
                  <th scope="col">Source</th>
                  <th scope="col">Definition</th>
                </tr>
              </thead>
              <tbody>
                {c.individuals.map((i) => (
                  <tr key={i.id}>
                    <td class="mc-num">{i.order}</td>
                    <th scope="row">{i.id}</th>
                    <td>{i.source}</td>
                    <td>{i.label === null ? i.definition : `${i.label}. ${i.definition}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <h3 class="mc-h">Constraints</h3>
        {c.constraints.length === 0 ? (
          <p class="mc-hint">No constraint is declared on {c.id}.</p>
        ) : (
          <div class="mc-scroll mc-focus" role="region" aria-label="Constraints table" tabIndex={0}>
            <table class="mc-table on-table" aria-label={`Constraints on ${c.id}`}>
              <thead>
                <tr>
                  <th scope="col">Constraint</th>
                  <th scope="col">Kind</th>
                  <th scope="col">Source</th>
                  <th scope="col">Text</th>
                </tr>
              </thead>
              <tbody>
                {c.constraints.map((x) => {
                  const rule = constraintRule(x);
                  return (
                    <tr key={x.id}>
                      <th scope="row">{x.id}</th>
                      <td>
                        <span class="on-kind">{x.kind}</span>
                      </td>
                      <td>{x.source}</td>
                      <td>
                        {x.text}
                        {rule && <span class="on-rule">{rule}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p class="mc-hint">
          A checkable constraint is a rule a program runs on every record; a manual one needs a person (or a model) to judge the text. Source is the
          labelling guide's rule id.
        </p>
      </div>
      <style>{ONTOLOGY_CSS}</style>
    </section>
  );
}
