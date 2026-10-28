// The AI Agents track's teaching aids, one module per aid: types, formats and shared step text
// (agentsAidsTypes), PatternStepper (agentsAidsPatterns), AgentTrace (agentsAidsTrace), CostCompare
// (agentsAidsCost) and EngineScorecard (agentsAidsScores). All pure and browser-safe; agentsData.ts reads
// the files at build time and passes the islands only what they show. Import from here.
export * from './agentsAidsTypes';
export * from './agentsAidsPatterns';
export * from './agentsAidsTrace';
export * from './agentsAidsCost';
export * from './agentsAidsScores';
