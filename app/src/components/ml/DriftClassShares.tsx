// The class-share panel of DriftChart: real CFPB shares per year against the committed sample.
import { useState } from "preact/hooks";
import { pct, type DriftChartProps } from "../../lib/mlAids";

export function ClassShares({ p }: { p: DriftChartProps }) {
  const [year, setYear] = useState(p.years[p.years.length - 1]);
  const yi = p.years.indexOf(year);
  return (
    <div class="dc-shares">
      <p class="dc-eyebrow">
        Class shares per year · real CFPB vs the committed sample
      </p>
      <div class="dc-group" role="group" aria-label="Class shares: year">
        {p.years.map((y) => (
          <button
            key={y}
            type="button"
            class="dc-toggle"
            aria-pressed={y === year}
            onClick={() => setYear(y)}
          >
            {y}
          </button>
        ))}
      </div>
      <div
        class="dc-scroll"
        role="region"
        aria-label="Class shares table"
        tabIndex={0}
      >
        <table
          class="dc-table dc-share-table"
          aria-label={`Class shares in ${year}: real vs sample`}
        >
          <thead>
            <tr>
              <th scope="col">Category</th>
              <th scope="col">Real (solid)</th>
              <th scope="col">Sample (hatched)</th>
            </tr>
          </thead>
          <tbody>
            {p.classes.map((c, i) => (
              <tr key={c}>
                <th scope="row">{c}</th>
                <td>
                  <span class="dc-bar dc-bar-real" aria-hidden="true">
                    <span style={{ width: `${p.real[i][yi] * 100}%` }} />
                  </span>
                  {pct(p.real[i][yi])}
                </td>
                <td>
                  <span class="dc-bar dc-bar-sample" aria-hidden="true">
                    <span style={{ width: `${p.sample[i][yi] * 100}%` }} />
                  </span>
                  {pct(p.sample[i][yi])}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="dc-hint">
        The sample is stratified by design, so it mutes the real shift in
        class mix (bars run from 0 to 100%). Drift measured on the sample
        cannot see that shift; on the full stream the predictions signal
        could, against a null sized for that volume and drawn the way that
        stream is sampled.
      </p>
    </div>
  );
}
