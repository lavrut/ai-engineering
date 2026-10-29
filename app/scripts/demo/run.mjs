#!/usr/bin/env node
// `npm run demo [-- --dry] [-- --out DIR]`: record, then encode, with the same arguments for both.
// (npm hands `--` arguments only to the last command of an `a && b` script, so one entry point parses
// them once.) A dry run records nothing, so there is nothing to encode.
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { main as encode } from './encode.mjs';
import { main as record } from './record.mjs';

/** The steps for these arguments: `[['record', argv], ['encode', argv]]`, or only record for `--dry`. */
export function plan(argv) {
  const steps = [['record', argv]];
  if (!argv.includes('--dry')) steps.push(['encode', argv]);
  return steps;
}

export async function main(argv = process.argv.slice(2)) {
  const run = { record, encode };
  for (const [step, args] of plan(argv)) {
    const code = await run[step](args);
    if (code !== 0) return code;
  }
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then((code) => process.exit(code), (err) => { console.error(err.message); process.exit(1); });
}
