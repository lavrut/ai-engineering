import type { MarkView } from '../../lib/ontologyAids';

const TONE = { ok: 'dt-mark-ok', fail: 'dt-mark-fail', warn: 'on-mark-warn' } as const;

/** ✓ AGREE, ✗ FAIL, ? MISSING: a symbol and a word, with the colour only as a border. */
export default function OntologyMark({ mark }: { mark: MarkView }) {
  return (
    <span class={`dt-mark ${TONE[mark.tone]}`}>
      {mark.icon} {mark.word}
    </span>
  );
}
