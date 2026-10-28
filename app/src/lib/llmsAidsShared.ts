// Shared by the LLMs aid modules: the allowed answers, the default runs, snippets and run labels.
// Pure and browser-safe; re-exported by llmsAids.ts.
import type { RunConfig, Ticket } from './llmsData';

/** The allowed answers, in triage_lab.py's order (section 1). */
export const CATEGORIES = ['billing', 'bug', 'account', 'how_to', 'feature_request', 'outage'] as const;
export const PRIORITIES = ['low', 'normal', 'high', 'urgent'] as const;
/** The confusion-matrix column for a line with no answer (refusal, cut-off or error). */
export const NONE = '(none)';
export const RECORD_COMMAND = 'python triage_lab.py record --plan all';
export const SNIPPET_CHARS = 140;
/** triage_lab.py's default --workers. */
export const WORKERS = 8;

export const DEFAULT_MATRIX_RUN = 'triage__claude-haiku-5-5__v1_basic__default';
export const DEFAULT_SIM_RUN = 'triage__claude-haiku-5-5__v2_rules__default';

export type Split = 'dev' | 'test' | 'all';

/** The first 140 characters, with an ellipsis when cut. */
export function snippet(text: string, n = SNIPPET_CHARS): string {
  return text.length > n ? `${text.slice(0, n)}…` : text;
}


export const MODEL_NAMES: Record<string, string> = {
  'claude-haiku-5-5': 'Haiku 5.5',
  'claude-sonnet-5-5': 'Sonnet 5.5',
  'claude-opus-5-5': 'Opus 5.5',
};

/** "Haiku 5.5 · v1_basic", plus " · high effort" when an effort was sent. */
export function runLabel(config: RunConfig): string {
  const parts = [MODEL_NAMES[config.model] ?? config.model, config.prompt_version];
  if (config.effort) parts.push(`${config.effort} effort`);
  return parts.join(' · ');
}

export const inSplit = (t: Ticket, split: Split) => split === 'all' || t.split === split;
