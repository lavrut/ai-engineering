import type { Diagram } from '../../lib/diagram';

// Taxonomy, schema, ontology, knowledge graph: four layers of saying what things are, each adding to the one before,
// top to bottom (the label on the edge between two layers, drawn to its left, says what the lower one adds). On the right, where this
// course already has each one: the ML track's category map, the data contracts, Lumen's ontology and the data
// fabric's lineage graph, each joined to its layer. diagrams.test.ts pins every number a caption states against
// ontology.json, lineage.json and the category map. Each linked caption ends with its section.
const LAYER_X = 250;
const LAYER_W = 230;
const ART_X = 620;
const ART_W = 360;
const layer = (id: string, label: string, y: number) => ({ id, label, role: 'memory' as const, x: LAYER_X, y, w: LAYER_W });
const artefact = (id: string, label: string, y: number) => ({ id, label, role: 'dataset' as const, x: ART_X, y, w: ART_W });
const edge = (from: string, to: string, label?: string) => ({ id: `e-${from}-${to}`, from, to, ...(label ? { label } : {}) });

export const ontologyLayers: Diagram = {
  id: 'ontology-layers',
  title: "Taxonomy, schema, ontology, knowledge graph: what each layer adds, and where the course's artefacts sit",
  width: 1000,
  height: 560,
  nodes: [
    layer('taxonomy', 'Taxonomy', 30),
    layer('schema', 'Schema', 170),
    layer('ontology', 'Ontology', 310),
    layer('knowledge-graph', 'Knowledge graph', 450),
    artefact('category-map', 'ml/data/category_map.json', 30),
    artefact('data-contracts', 'data/contracts/*.yaml', 170),
    artefact('lumen-ontology', 'ontology/lumen.yaml', 310),
    artefact('lineage-graph', 'data/data/derived/lineage.json', 450),
  ],
  edges: [
    edge('taxonomy', 'schema', '+ fields and types'),
    edge('schema', 'ontology', '+ classes, relations, rules'),
    edge('ontology', 'knowledge-graph', '+ the facts themselves'),
    edge('category-map', 'taxonomy'),
    edge('data-contracts', 'schema'),
    edge('lumen-ontology', 'ontology'),
    edge('lineage-graph', 'knowledge-graph'),
  ],
  steps: [
    {
      n: 1,
      highlight: ['taxonomy', 'e-category-map-taxonomy', 'category-map'],
      caption:
        "A taxonomy names things and sorts them: a controlled vocabulary, perhaps a hierarchy. The ML track's category map is one: 6 product names that the published complaint products map onto. It says what the names are, not what they mean or how they relate. In Lumen's ontology they become the 6 individuals of Product. In the code: section 4.",
      code: { file: 'python', section: 4, source: 'ontology-lumen' },
    },
    {
      n: 2,
      highlight: ['taxonomy', 'e-taxonomy-schema', 'schema', 'e-data-contracts-schema', 'data-contracts'],
      caption:
        'A schema adds fields and types: a ticket has a category, a string from a closed list, and it is required. The data contracts are schemas, with quality and freshness rules on top. Lumen\'s ontology states its 6 properties the same way, each with a range and a cardinality. In the code: section 2.',
      code: { file: 'python', section: 2, source: 'ontology-lumen' },
    },
    {
      n: 3,
      highlight: ['schema', 'e-schema-ontology', 'ontology', 'e-lumen-ontology-ontology', 'lumen-ontology'],
      caption:
        'An ontology adds meaning: classes with definitions, the relations between them (a ticket has exactly one category; a reply answers one ticket), and named individuals in a set order. ontology/lumen.yaml has 9 classes, 5 relations and 27 individuals. In the code: section 3.',
      code: { file: 'python', section: 3, source: 'ontology-lumen' },
    },
    {
      n: 4,
      highlight: ['ontology', 'e-lumen-ontology-ontology', 'lumen-ontology'],
      caption:
        'It also adds rules: Lumen\'s ontology has 25 constraints: 1 a program can check, 24 a person must judge. Each names the labelling guide rule it came from, so the guide and the file can be checked against each other. In the code: section 5.',
      code: { file: 'python', section: 5, source: 'ontology-lumen' },
    },
    {
      n: 5,
      highlight: ['ontology', 'e-ontology-knowledge-graph', 'knowledge-graph', 'e-lineage-graph-knowledge-graph', 'lineage-graph'],
      caption:
        "A knowledge graph holds the facts themselves: things and the links between them, typed by an ontology. The data fabric's lineage graph is a small one: 27 nodes and 32 edges, each saying which track writes or reads which dataset. This track does not build a graph; it is where an ontology goes next.",
    },
    {
      n: 6,
      highlight: ['lumen-ontology', 'e-lumen-ontology-ontology', 'ontology', 'e-schema-ontology', 'schema', 'e-data-contracts-schema', 'data-contracts'],
      caption:
        'One source of truth: generate.py builds the prompt definitions, the JSON schema and the MCP tool from the ontology, and audit.py checks every artefact against it, fact by fact. The gate is red while any fact disagrees. In the code: section 6.',
      code: { file: 'python', section: 6, source: 'ontology-audit' },
    },
  ],
};
