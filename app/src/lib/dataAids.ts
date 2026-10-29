// The Data Fabric & Data Contracts track's teaching aids, one module per aid: file shapes, formats and verdict
// text (dataAidsTypes), CatalogExplorer (dataAidsCatalog), QualityReport (dataAidsQuality), ContractViewer
// (dataAidsContract) and LineageDiff (dataAidsLineage). All pure and browser-safe; dataData.ts reads the files
// at build time and passes the islands only what they show. Import from here.
export * from './dataAidsTypes';
export * from './dataAidsCatalog';
export * from './dataAidsQuality';
export * from './dataAidsContract';
export * from './dataAidsLineage';
