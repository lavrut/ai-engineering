// drift — section 6 of ml-serve: monitor.py's PSI drift, recomputed in Go.
//
// Run (from the repo root):
//
//	go -C ml/go run . drift
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"time"
)

// ── 6. Drift ────────────────────────────────────────────────────────────────
// Recomputes monitor.py's PSI per month and quarter against the 2022 reference. Settings
// and alert thresholds come from drift.json: its null band came from numpy's RNG, which
// Go cannot repeat, so the thresholds are read, not recomputed.
type complaint struct{ Date, Narrative string }

type Shifted struct {
	Word         string  `json:"word"`
	Contribution float64 `json:"contribution"`
	Direction    string  `json:"direction"`
}

type Period struct {
	Key                              string
	N                                int
	PSIPredictions, PSIWords         float64
	AlertConvention, AlertCalibrated bool
	TopShiftedWords                  []Shifted
}

type threshold struct {
	Threshold float64 `json:"threshold"`
}
type nullBand struct {
	N                  int `json:"n"`
	Predictions, Words threshold
}

type driftSettings struct {
	AlertAt      float64  `json:"alert_at"`
	Epsilon      float64  `json:"epsilon"`
	MinN         int      `json:"min_n"`
	TopK         int      `json:"top_k"`
	ModelVersion string   `json:"model_version"`
	StopWords    []string `json:"stop_words"`
}

type DriftResult struct {
	Settings         driftSettings              `json:"settings"`
	Null             map[string]json.RawMessage `json:"null"`
	Months, Quarters []Period                   `json:"-"`
}

func proportions(counts []float64, eps float64) []float64 {
	total, out := pySum(counts), make([]float64, len(counts))
	for i, c := range counts {
		if total != 0 {
			out[i] = c / total
		}
		out[i] += eps // added after dividing, never renormalised
	}
	return out
}

// contributions is each bucket's share of the PSI: (a - e) * ln(a / e), never negative.
func contributions(expected, actual []float64, eps float64) []float64 {
	e, a := proportions(expected, eps), proportions(actual, eps)
	out := make([]float64, len(e))
	for i := range e {
		out[i] = (a[i] - e[i]) * math.Log(a[i]/e[i])
	}
	return out
}

func psi(expected, actual []float64, eps float64) float64 {
	return pySum(contributions(expected, actual, eps))
}

// loadComplaints reads complaints.jsonl into the train / test / stream splits.
func loadComplaints(path string) (map[string][]complaint, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	out := map[string][]complaint{}
	sc := bufio.NewScanner(f)
	sc.Buffer(nil, 1<<24)
	for sc.Scan() {
		if strings.TrimSpace(sc.Text()) == "" {
			continue
		}
		var c complaint
		if err := json.Unmarshal(sc.Bytes(), &c); err != nil {
			return nil, err
		}
		if _, err := time.Parse("2006-01-02", c.Date); err != nil {
			return nil, fmt.Errorf("date %q is not YYYY-MM-DD", c.Date)
		}
		name := ""
		for _, s := range splits {
			if s[1] <= c.Date && c.Date <= s[2] {
				name = s[0]
				break
			}
		}
		if name == "" {
			return nil, fmt.Errorf("date %s is outside every split", c.Date)
		}
		out[name] = append(out[name], c)
	}
	return out, sc.Err()
}

// topWords: the k most frequent training words the model knows, stop words out, ties alphabetical.
func topWords(rows []complaint, m *Model, stop map[string]bool, k int) []string {
	freq := map[string]int{}
	for _, r := range rows {
		for _, t := range tokenize(r.Narrative, &m.Tokenizer) {
			if _, known := m.index[t]; known && !stop[t] {
				freq[t]++
			}
		}
	}
	words := make([]string, 0, len(freq))
	for w := range freq {
		words = append(words, w)
	}
	sort.Slice(words, func(i, j int) bool {
		a, b := words[i], words[j]
		return freq[a] > freq[b] || freq[a] == freq[b] && a < b
	})
	return words[:min(k, len(words))]
}

// buckets holds the word buckets and the 2022 reference sums every period is compared with.
type buckets struct {
	m                 *Model
	words             []string
	slot              map[string]int
	stop              map[string]bool
	expPred, expWords []float64
}

// featurize sums a block of complaints: predicted-class counts, and token counts per word
// bucket (the top words, then "other"; stop words not counted).
func (b *buckets) featurize(rows []complaint) (pred, words []float64) {
	pred, words = make([]float64, len(b.m.Classes)), make([]float64, len(b.words)+1)
	for _, r := range rows {
		pred[slices.Index(b.m.Classes, b.m.score(r.Narrative).Label)]++
		for _, t := range tokenize(r.Narrative, &b.m.Tokenizer) {
			if j, ok := b.slot[t]; ok {
				words[j]++
			} else if !b.stop[t] {
				words[len(b.words)]++ // "other"
			}
		}
	}
	return pred, words
}

// period scores one month or quarter, as monitor.drift does.
func (b *buckets) period(key string, rows []complaint, s driftSettings, null nullBand) Period {
	actPred, actWords := b.featurize(rows)
	parts := contributions(b.expWords, actWords, s.Epsilon)
	n := len(rows)
	big := n >= s.MinN
	p := Period{Key: key, N: n, PSIPredictions: psi(b.expPred, actPred, s.Epsilon), PSIWords: pySum(parts)}
	p.AlertConvention = big && (p.PSIPredictions >= s.AlertAt || p.PSIWords >= s.AlertAt)
	p.AlertCalibrated = big && (p.PSIPredictions > null.Predictions.Threshold || p.PSIWords > null.Words.Threshold)
	order := make([]int, len(b.words)) // word buckets by contribution, ties alphabetical
	for i := range order {
		order[i] = i
	}
	sort.Slice(order, func(x, y int) bool {
		i, j := order[x], order[y]
		return parts[i] > parts[j] || parts[i] == parts[j] && b.words[i] < b.words[j]
	})
	ePct, aPct := proportions(b.expWords, s.Epsilon), proportions(actWords, s.Epsilon)
	for _, i := range order[:min(shownWords, len(order))] {
		dir := "down"
		if aPct[i] > ePct[i] {
			dir = "up"
		}
		p.TopShiftedWords = append(p.TopShiftedWords, Shifted{b.words[i], parts[i], dir})
	}
	return p
}

func periodKey(date, unit string) string {
	if unit == "month" {
		return date[:7]
	}
	month := int(date[5]-'0')*10 + int(date[6]-'0')
	return fmt.Sprintf("%s-Q%d", date[:4], (month-1)/3+1)
}

func computeDrift(root string, m *Model) (*DriftResult, error) {
	raw, err := os.ReadFile(filepath.Join(root, "reports", "drift.json"))
	var rep DriftResult
	if err == nil {
		err = json.Unmarshal(raw, &rep)
	}
	if err != nil {
		return nil, fmt.Errorf("drift.json: %v", err)
	}
	if rep.Settings.ModelVersion != m.Version {
		return nil, fmt.Errorf("drift.json is for %s, production is %s", rep.Settings.ModelVersion, m.Version)
	}
	data, err := loadComplaints(filepath.Join(root, "data", "complaints.jsonl"))
	if err != nil {
		return nil, err
	}
	b := &buckets{m: m, stop: setOf(rep.Settings.StopWords), slot: map[string]int{}}
	b.words = topWords(data["train"], m, b.stop, rep.Settings.TopK) // chosen from training
	for i, w := range b.words {
		b.slot[w] = i
	}
	b.expPred, b.expWords = b.featurize(data["test"]) // the reference: 2022
	for _, unit := range []string{"month", "quarter"} {
		var null nullBand
		if err := json.Unmarshal(rep.Null[unit], &null); err != nil {
			return nil, fmt.Errorf("drift.json null.%s: %v", unit, err)
		}
		groups := map[string][]complaint{}
		for _, r := range data["stream"] {
			groups[periodKey(r.Date, unit)] = append(groups[periodKey(r.Date, unit)], r)
		}
		keys := make([]string, 0, len(groups))
		for k := range groups {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, key := range keys {
			if n := len(groups[key]); n >= rep.Settings.MinN && n != null.N {
				return nil, fmt.Errorf("drift.json has no %s null band for n=%d (only n=%d)", unit, n, null.N)
			}
			p := b.period(key, groups[key], rep.Settings, null)
			if unit == "month" {
				rep.Months = append(rep.Months, p)
			} else {
				rep.Quarters = append(rep.Quarters, p)
			}
		}
	}
	return &rep, nil
}

func printDrift(rep *DriftResult) {
	for _, unit := range []string{"month", "quarter"} {
		var null nullBand
		json.Unmarshal(rep.Null[unit], &null)
		periods := map[string][]Period{"month": rep.Months, "quarter": rep.Quarters}[unit]
		fmt.Printf("null threshold at n=%d: predictions %.4f, words %.4f\n", null.N,
			null.Predictions.Threshold, null.Words.Threshold)
		fmt.Printf("%-9s%4s%10s%11s  conv  calib  top shifted words\n", unit, "n", "psi_pred", "psi_words")
		yes := map[bool]string{true: "YES", false: "-"}
		for _, p := range periods {
			top := make([]string, len(p.TopShiftedWords))
			for i, w := range p.TopShiftedWords {
				top[i] = w.Word + map[string]string{"up": "+", "down": "-"}[w.Direction]
			}
			fmt.Printf("%-9s%4d%10.4f%11.4f  %-6s%-7s%s\n", p.Key, p.N, p.PSIPredictions, p.PSIWords,
				yes[p.AlertConvention], yes[p.AlertCalibrated], strings.Join(top, ", "))
		}
	}
}
