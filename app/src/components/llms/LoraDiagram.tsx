import { useState } from 'preact/hooks';
import { LORA_D, LORA_RANK, loraParams } from '../../lib/llmsAids';

const num = (n: number) => n.toLocaleString('en-US');

/** W (d × d, frozen) beside the two trained adapter matrices A (d × r) and B (r × d), drawn to scale. */
export default function LoraDiagram({ d = LORA_D, initial = LORA_RANK.initial }: { d?: number; initial?: number }) {
  const [r, setR] = useState(initial);
  const { trainable, full, share } = loraParams(d, r);

  const S = 130; // the side of W, in viewBox units
  const thin = Math.max(2, (S * r) / d); // A's width and B's height, to scale (at least 2 so r = 1 is visible)
  const top = 34;
  const ax = 170;
  const bx = ax + thin + 20;

  return (
    <section class="lora" aria-label="LoRA parameter count">
      <p class="lora-eyebrow">LoRA · one {d} × {d} matrix (Qwen2.5-0.5B hidden size)</p>
      <div class="lora-figure">
        <svg
          viewBox="0 0 340 190"
          role="img"
          aria-label={`W is ${d} by ${d} and frozen. Beside it, A is ${d} by ${r} and B is ${r} by ${d}; only A and B are trained. Drawn to scale.`}
        >
          <text class="lora-name" x={10} y={top - 10}>
            W · frozen
          </text>
          <rect class="lora-frozen" x={10} y={top} width={S} height={S} />
          <text class="lora-dim" x={10 + S / 2} y={top + S + 16} text-anchor="middle">
            {d} × {d}
          </text>

          <text class="lora-op" x={155} y={top + S / 2 + 6} text-anchor="middle">
            +
          </text>

          <text class="lora-name" x={ax} y={top - 10}>
            A
          </text>
          <rect class="lora-trained" x={ax} y={top} width={thin} height={S} />
          <text class="lora-dim" x={ax} y={top + S + 16}>
            A: {d} × {r}
          </text>

          <text class="lora-name" x={bx} y={top - 10}>
            B
          </text>
          <text class="lora-dim" x={bx} y={top + S - 4}>
            A and B: trained
          </text>
          <rect class="lora-trained" x={bx} y={top} width={S} height={thin} />
          <text class="lora-dim" x={bx} y={top + thin + 16}>
            B: {r} × {d}
          </text>
        </svg>
      </div>

      <div class="lora-control">
        <label for="lora-rank" class="lora-label">
          Rank r <span class="lora-value">{r}</span>
        </label>
        <input
          id="lora-rank"
          type="range"
          min={LORA_RANK.min}
          max={LORA_RANK.max}
          step={1}
          value={r}
          aria-valuetext={`rank ${r}`}
          onInput={(e) => setR(Number((e.target as HTMLInputElement).value))}
        />
      </div>

      <div class="lora-out" role="status">
        <p>
          <strong>Trained: 2 · d · r = 2 × {d} × {r} = {num(trainable)}</strong> numbers in A and B.
        </p>
        <p>
          Full fine-tuning would train d² = {num(full)} numbers in W, so LoRA trains{' '}
          <strong>{(share * 100).toFixed(2)}%</strong> of them.
        </p>
      </div>
      <div class="lora-bar" aria-hidden="true">
        <span class="lora-bar-fill" style={{ width: `${Math.max(0.5, share * 100)}%` }} />
      </div>
      <p class="lora-note">
        The bar is W&apos;s {num(full)} numbers; the filled part is what LoRA trains. lora.py uses r = 8 on q_proj, k_proj,
        v_proj and o_proj. In this model k_proj and v_proj are {d} × 128, so their adapters are smaller than this one.
      </p>
      <style>{css}</style>
    </section>
  );
}

const css = `
.lora { margin: 32px 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.lora-eyebrow, .lora-label {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-label); text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted);
}
.lora-figure { min-width: 0; padding: 8px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
.lora-figure svg { display: block; width: 100%; max-width: 520px; height: auto; margin: 0 auto; }
.lora-frozen { fill: var(--surface-2); stroke: var(--muted); stroke-width: 1; stroke-dasharray: 4 3; }
.lora-trained { fill: var(--accent); stroke: var(--accent); stroke-width: 1; }
.lora-name { font-family: var(--font-mono); font-size: 12px; font-weight: 600; fill: var(--ink); }
.lora-dim { font-family: var(--font-mono); font-size: 11px; fill: var(--muted); }
.lora-op { font-family: var(--font-mono); font-size: 20px; fill: var(--ink); }
.lora-control { display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; }
.lora-value { color: var(--ink); font-weight: 600; }
.lora-control input { width: 100%; accent-color: var(--accent); }
.lora-control input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.lora-out {
  padding: 10px 14px; font-size: var(--text-small); color: var(--ink);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
}
.lora-out p { margin: 0; }
.lora-out p + p { margin-top: 4px; }
.lora-out strong { font-family: var(--font-mono); font-size: 0.875rem; }
.lora-bar { height: 12px; background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
.lora-bar-fill { display: block; height: 100%; background: var(--accent); }
.lora-note { margin: 0; font-size: var(--text-small); color: var(--muted); }
`;
