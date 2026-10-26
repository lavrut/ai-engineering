import type { Diagram } from '../../lib/diagram';

// Layout (default node 150x56). Row 1 runs left to right: Complaints, Model server, Monitor,
// Retrain. Row 2 snakes back right to left: Gate under Retrain, then Registry under Monitor.
// The loop edge leaves Registry to the left and rises into the bottom of the server: the
// server always loads whatever the registry names production. Rollback drops straight down
// from Monitor to Registry. Code links: ml-serve (server.py / Go main.go), ml-monitor
// (monitor.py / Go drift.go), ml-mlops (mlops.py, Python only).
export const mlLoop: Diagram = {
  id: 'ml-loop',
  title: 'The model in production, step by step',
  width: 990,
  height: 330,
  nodes: [
    { id: 'complaints', label: 'New complaints', role: 'dataset', x: 30, y: 60 },
    { id: 'server', label: 'Model server', role: 'harness', x: 290, y: 60 },
    { id: 'monitor', label: 'Drift monitor', role: 'logs', x: 550, y: 60 },
    { id: 'retrain', label: 'Retrain', role: 'tool', x: 810, y: 60 },
    { id: 'gate', label: 'Promotion gate', role: 'grader', x: 810, y: 230 },
    { id: 'registry', label: 'Model registry', role: 'memory', x: 550, y: 230 },
  ],
  edges: [
    { id: 'e-complaint', from: 'complaints', to: 'server', label: 'text' },
    { id: 'e-predictions', from: 'server', to: 'monitor', label: 'labels' },
    { id: 'e-alert', from: 'monitor', to: 'retrain', label: 'alert' },
    { id: 'e-candidate', from: 'retrain', to: 'gate', label: 'candidate' },
    { id: 'e-promote', from: 'gate', to: 'registry', label: 'promote' },
    { id: 'e-load', from: 'registry', to: 'server', label: 'production model', loop: true },
    { id: 'e-rollback', from: 'monitor', to: 'registry', label: 'rollback' },
  ],
  steps: [
    {
      n: 1,
      highlight: ['complaints', 'e-complaint', 'server'],
      caption:
        'The server answers POST /predict with a label, the six scores and a low-confidence flag. Python and Go serve the same model file and give the same numbers.',
      code: { file: 'python', section: 4, source: 'ml-serve', goSection: 5 },
    },
    {
      n: 2,
      highlight: ['server', 'e-predictions', 'monitor'],
      caption:
        'Each month, the monitor compares the predicted categories and the words used with 2022’s, as a PSI. It alerts only when PSI is above what sampling noise alone produces.',
      code: { file: 'python', section: 4, source: 'ml-monitor', goSection: 6 },
    },
    {
      n: 3,
      highlight: ['monitor', 'e-alert', 'retrain'],
      caption:
        'A real shift is a reason to retrain: the same settings on a newer window (2019–2022), logged as a run with its data version and model hash.',
      code: { file: 'python', section: 4, source: 'ml-mlops' },
    },
    {
      n: 4,
      highlight: ['retrain', 'e-candidate', 'gate'],
      caption:
        'The gate scores the candidate and production on months neither trained on. The candidate must win by more than the noise band, with no category below the floor. v2 gained 0.005 macro-F1, inside the 0.058 band, so it stayed in staging.',
      code: { file: 'python', section: 3, source: 'ml-mlops' },
    },
    {
      n: 5,
      highlight: ['gate', 'e-promote', 'registry', 'e-load', 'server'],
      caption:
        'A candidate that passes is promoted: the registry names it production, and the server loads it when it restarts. Every move is kept in the registry’s history.',
      code: { file: 'python', section: 2, source: 'ml-mlops' },
    },
    {
      n: 6,
      highlight: ['monitor', 'e-rollback', 'registry', 'e-load', 'server'],
      caption:
        'If a promoted model does worse in production, roll back: the registry makes the previous version production again in one atomic write, and the server loads it when it restarts.',
      code: { file: 'python', section: 2, source: 'ml-mlops' },
    },
  ],
};
