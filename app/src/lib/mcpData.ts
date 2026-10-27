// The MCP track's data, read at build time from mcp/data/derived/ (written by
// mcp/python/derive.py from the real servers and the approved recordings).
//
// Build time only: the eager globs turn the files into imports, so an island must never import
// this module. The types and the logic are in mcpAids.ts (pure, browser-safe); pass an island
// the small props that mcpAids builds from this.
import type { AppSnapshotJson, ElicitationJson, InjectionJson, McpData, MessagesJson, ToolBudgetJson } from './mcpAids';

export * from './mcpAids';

let cache: McpData | undefined;

export function loadMcpData(): McpData {
  if (!cache) {
    const raw = import.meta.glob('../../../mcp/data/derived/*.json', { import: 'default', eager: true }) as Record<string, unknown>;
    const get = <T>(name: string): T => {
      const key = Object.keys(raw).find((k) => k.endsWith(`/derived/${name}.json`));
      if (!key) throw new Error(`mcpData: mcp/data/derived/${name}.json is missing (python mcp/python/derive.py writes it)`);
      return raw[key] as T;
    };
    cache = {
      messages: get<MessagesJson>('messages'),
      toolBudget: get<ToolBudgetJson>('tool_budget'),
      elicitation: get<ElicitationJson>('elicitation'),
      appSnapshot: get<AppSnapshotJson>('app_snapshot'),
      injection: get<InjectionJson>('injection'),
    };
  }
  return cache;
}
