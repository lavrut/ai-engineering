// The MCP track's teaching aids, one module per aid group: types and helpers (mcpTypes), the message
// view and tool budget (mcpMessages), the elicitation stepper (mcpElicitation), the App preview
// (mcpAppPreview) and the injection lab (mcpInjection). All pure and browser-safe; mcpData.ts reads
// the files at build time and passes the islands only what they show. Import from here.
export * from './mcpTypes';
export * from './mcpMessages';
export * from './mcpElicitation';
export * from './mcpAppPreview';
export * from './mcpInjection';
