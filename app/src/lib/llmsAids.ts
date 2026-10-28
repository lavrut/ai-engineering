// Logic for the LLMs track's aids: evaluation (DatasetBrowser, ConfusionMatrix,
// EvalRunSim), judging (JudgeCard, AgreementView, LabelingPage) and tuning
// (PromptAB, CostQualityChart, LoraDiagram). Pure and
// browser-safe; cache keys come in as a function so node:crypto stays out. The *Props builders run in the .astro
// wrappers at build time and keep only the small derived data each island needs
// (ruling R30): never whole runs or saved lines.
//
// One module per aid group, re-exported here: llmsAidsShared (answers, defaults, labels), llmsAidsEval,
// llmsAidsJudge and llmsAidsTune. Import from here.
export * from './llmsAidsShared';
export * from './llmsAidsEval';
export * from './llmsAidsJudge';
export * from './llmsAidsTune';
