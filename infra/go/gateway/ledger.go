// ledger: the Go twin of infra/python/ledger.py sections 1-2, what a call may cost and what it
// did cost: the lab's price table, worst_case_usd and cost_of, the same arithmetic in the same order.
package main

import (
	"fmt"
)

// ── 1. Prices ───────────────────────────────────────────────────────────────

// LabPrices is the LLMs lab's table, dollars per million tokens (input, output), copied from
// llms/python/triage_lab.py lines 47-52 (PRICES): Go cannot import Python, and the price
// "lab" in the config means this table. Python reads it from the lab; the twins must agree.
func LabPrices() map[string][2]float64 {
	return map[string][2]float64{
		"claude-haiku-5-5":  {0.10, 0.50},
		"claude-sonnet-5-5": {2.00, 10.00},
		"claude-opus-5-5":   {4.00, 20.00},
	}
}

// ResolvePrices is ledger.resolve_prices: "lab" becomes the lab's pair; a model priced lab
// that the lab does not list is an error naming it.
func ResolvePrices(cfg Config) (map[string][2]float64, error) {
	lab, out := LabPrices(), map[string][2]float64{}
	for n, m := range cfg.Models {
		if !m.Lab {
			out[n] = m.Price
			continue
		}
		p, ok := lab[n]
		if !ok {
			return nil, fmt.Errorf("models.%s.price is lab, but the lab's table has no %s", n, n)
		}
		out[n] = p
	}
	return out, nil
}

// ── 2. Cost ─────────────────────────────────────────────────────────────────

// WorstCaseUSD is ledger.worst_case_usd: no caching, every output token; 0 for an unpriced model.
func WorstCaseUSD(model string, in, out int, prices map[string][2]float64) float64 {
	p, ok := prices[model]
	if !ok {
		return 0
	}
	return (float64(in)*p[0] + float64(out)*p[1]) / 1_000_000
}

// costOf is ledger.cost_of: cache writes at 1.25x the input price, reads at 0.10x.
func costOf(model string, u map[string]int, prices map[string][2]float64) *float64 {
	p, ok := prices[model]
	if !ok {
		return nil
	}
	c := (float64(u["input_tokens"])*p[0] + float64(u["cache_creation_input_tokens"])*1.25*p[0] +
		float64(u["cache_read_input_tokens"])*0.10*p[0] + float64(u["output_tokens"])*p[1]) / 1_000_000
	return &c
}
