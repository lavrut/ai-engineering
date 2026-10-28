// Logic for the ML track's aids: the basics, ComplaintExplorer (lesson 2), FeatureExplorer (3),
// TrainingStepper (4) and MetricsView (5); and MLOps, ModelVsLlm (6), RunTable (7),
// RegistryTimeline (8) and DriftChart (10). Pure and browser-safe: it imports values only
// from mlExplore.ts, and only types from mlData.ts (whose globs must never reach an island).
// The *Props builders run in the .astro wrappers at build time and keep only the small
// derived data each island needs.
//
// One module per aid group, re-exported here: mlAidsShared (names, percentages, rounding), mlAidsBasics
// (lessons 2-5), mlAidsOps (6-8) and mlAidsDrift (10). Import from here.
export * from './mlAidsShared';
export * from './mlAidsBasics';
export * from './mlAidsOps';
export * from './mlAidsDrift';
