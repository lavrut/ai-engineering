import type { MarkView } from '../../lib/infraAids';

const TONE = { ok: 'dt-mark-ok', fail: 'dt-mark-fail', warn: 'in-mark-warn', info: 'in-mark-info' } as const;

/** ✓ hit, ✗ deny, ◐ warn: a symbol and a word, with the colour only as a border. */
export default function InfraMark({ mark }: { mark: MarkView }) {
  return (
    <span class={`dt-mark ${TONE[mark.tone]}`}>
      {mark.icon} {mark.word}
    </span>
  );
}
