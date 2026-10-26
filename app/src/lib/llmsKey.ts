// Cache keys for saved responses, the same as cache_key in llms/python/triage_lab.py.
// Uses node:crypto, so import it only at build time (.astro frontmatter) or in tests,
// never from an island. The browser-safe parts live in llmsData.ts.
import { createHash } from 'node:crypto';
import { canonicalJson, judgeSystem, type Prompts } from './llmsData';

export type KeyRequest = {
  kind: string;
  model: string;
  effort: string | null;
  prompt_version: string;
  prompt_text: string;
  input: unknown;
  rep: number;
};

/** sha256 hex of the canonical JSON of everything that shapes the answer. */
export function cacheKey(request: KeyRequest): string {
  return createHash('sha256').update(canonicalJson(request), 'utf8').digest('hex');
}

export function triageKey(
  ticketText: string,
  variant: string,
  model: string,
  effort: string | null,
  rep: number,
  prompts: Prompts,
): string {
  const prompt_text = prompts.triage[variant];
  if (prompt_text === undefined) throw new Error(`unknown prompt ${variant}`);
  return cacheKey({ kind: 'triage', model, effort, prompt_version: variant, prompt_text, input: ticketText, rep });
}

/** The order ("before" | "after") is the prompt version, so a verdict only matches the order that produced it. */
export function judgeKey(ticketText: string, reply: string, order: string, model: string, prompts: Prompts): string {
  return cacheKey({
    kind: 'judge',
    model,
    effort: null,
    prompt_version: order,
    prompt_text: judgeSystem(prompts),
    input: { ticket: ticketText, reply },
    rep: 0,
  });
}
