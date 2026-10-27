package core

// classify — CFPB-style complaint text through the Track 3 production model.
//
// This is NOT used to label Lumen tickets: its categories are the complaint products, not
// the ticket categories. ml/go is package main and cannot be imported, so the loader,
// tokenizer and scorer are re-implemented here, rule for rule (ML ruling R12), and pinned
// to the same fixtures.

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

const (
	lowConfidence = 0.5
	tokenPattern  = "[a-z0-9']+"
)

// ── 1. Loading the production model ─────────────────────────────────────────
// mlRoot is ML_ROOT if set, else <repo root>/ml.
func mlRoot() string {
	if r := os.Getenv("ML_ROOT"); r != "" {
		return r
	}
	return filepath.Join(Root(), "ml")
}

var (
	modelOnce sync.Once
	modelVal  *Model
	modelErr  error
)

// loadModel loads the production model once.
func loadModel() (*Model, error) {
	modelOnce.Do(func() { modelVal, modelErr = loadProduction(mlRoot()) })
	return modelVal, modelErr
}

type Tokenizer struct {
	Lowercase     string   `json:"lowercase"`
	Pattern       string   `json:"pattern"`
	DropXRunsFrom int      `json:"drop_x_runs_from"`
	StopWords     []string `json:"stop_words"`
	stop          map[string]bool
}

type Model struct {
	Version       string         `json:"version"`
	Tokenizer     Tokenizer      `json:"tokenizer"`
	Vocabulary    []string       `json:"vocabulary"`
	IDF           []float64      `json:"idf"`
	Classes       []string       `json:"classes"`
	Coef          [][]float64    `json:"coef"`
	Intercept     []float64      `json:"intercept"`
	MajorityClass string         `json:"majority_class"`
	index         map[string]int // term -> position, built once
}

type registryEntry struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
	Stage  string `json:"stage"`
}

// productionEntry walks "versions" in file order, so the first production entry wins,
// as in Python (a Go map would visit them in random order).
func productionEntry(raw []byte) (string, registryEntry, error) {
	var reg struct {
		Versions json.RawMessage `json:"versions"`
	}
	if err := json.Unmarshal(raw, &reg); err != nil {
		return "", registryEntry{}, err
	}
	dec := json.NewDecoder(bytes.NewReader(reg.Versions))
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return "", registryEntry{}, errors.New(`"versions" is not an object`)
	}
	for dec.More() {
		key, err := dec.Token()
		var e registryEntry
		if err == nil {
			err = dec.Decode(&e)
		}
		if err != nil {
			return "", registryEntry{}, err
		}
		if e.Stage == "production" {
			return key.(string), e, nil
		}
	}
	return "", registryEntry{}, nil
}

// loadProduction fails exactly where server.load_production does: the server must not start.
func loadProduction(root string) (*Model, error) {
	regPath := filepath.Join(root, "registry.json")
	raw, err := os.ReadFile(regPath)
	if err != nil {
		return nil, fmt.Errorf("registry not found: %s (set ML_ROOT to the ml/ directory)", regPath)
	}
	version, entry, err := productionEntry(raw)
	if err != nil {
		return nil, fmt.Errorf("registry unreadable: %s (%v)", regPath, err)
	}
	if version == "" {
		return nil, fmt.Errorf("registry has no version in stage 'production': %s", regPath)
	}
	modelPath := filepath.Join(root, entry.Path)
	data, err := os.ReadFile(modelPath)
	if err != nil {
		return nil, fmt.Errorf("model file for %s not found: %s", version, modelPath)
	}
	sum := sha256.Sum256(data)
	if actual := hex.EncodeToString(sum[:]); actual != entry.SHA256 {
		return nil, fmt.Errorf("SHA-256 mismatch for %s: registry says %s, file is %s", version, entry.SHA256, actual)
	}
	var m Model
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, fmt.Errorf("model file for %s is not valid JSON: %s", version, modelPath)
	}
	if m.Version != version {
		return nil, fmt.Errorf("registry says production is %s, but the model file says %q", version, m.Version)
	}
	// Go implements the tokenizer rules in code, so the file must ask for exactly those rules.
	if m.Tokenizer.Lowercase != "ascii" || m.Tokenizer.Pattern != tokenPattern {
		return nil, fmt.Errorf("model %s wants tokenizer %q / %q; Go implements ascii / %s",
			version, m.Tokenizer.Lowercase, m.Tokenizer.Pattern, tokenPattern)
	}
	if len(m.IDF) != len(m.Vocabulary) || len(m.Coef) != len(m.Classes) || len(m.Intercept) != len(m.Classes) {
		return nil, fmt.Errorf("model %s: vocabulary, idf, classes, coef and intercept sizes disagree", version)
	}
	for _, row := range m.Coef {
		if len(row) != len(m.Vocabulary) {
			return nil, fmt.Errorf("model %s: a coef row does not match the vocabulary", version)
		}
	}
	m.Tokenizer.stop = setOf(m.Tokenizer.StopWords)
	m.index = make(map[string]int, len(m.Vocabulary))
	for i, t := range m.Vocabulary {
		m.index[t] = i
	}
	return &m, nil
}

func setOf(words []string) map[string]bool {
	s := make(map[string]bool, len(words))
	for _, w := range words {
		s[w] = true
	}
	return s
}

// ── 2. Tokenizer and TF-IDF ─────────────────────────────────────────────────
// tokenize repeats train.tokenize's rules byte by byte. Lowercasing touches A-Z only, and
// every byte outside [a-z0-9'] separates tokens: a multi-byte UTF-8 character is all bytes
// >= 0x80, so "café" gives "caf" exactly as in Python. No Unicode-aware functions here.
func tokenize(text string, cfg *Tokenizer) []string {
	b := []byte(text)
	var tokens []string
	start := -1
	for i := 0; i <= len(b); i++ {
		if i < len(b) {
			if 'A' <= b[i] && b[i] <= 'Z' {
				b[i] += 'a' - 'A'
			}
			if c := b[i]; 'a' <= c && c <= 'z' || '0' <= c && c <= '9' || c == '\'' {
				if start < 0 {
					start = i
				}
				continue
			}
		}
		if start >= 0 {
			tok := strings.Trim(string(b[start:i]), "'")
			xRun := len(tok) >= cfg.DropXRunsFrom && strings.Trim(tok, "x") == ""
			if tok != "" && !cfg.stop[tok] && !xRun {
				tokens = append(tokens, tok)
			}
			start = -1
		}
	}
	return tokens
}

// pyAcc adds floats the way Python 3.12+ sum() does (Neumaier compensation), so Go's
// sums round like Python's: sum([0.1] * 10) is exactly 1.0 in both.
type pyAcc struct{ s, c float64 }

func (a *pyAcc) add(x float64) {
	t := a.s + x
	if math.Abs(a.s) >= math.Abs(x) {
		a.c += (a.s - t) + x
	} else {
		a.c += (x - t) + a.s
	}
	a.s = t
}

func (a *pyAcc) value() float64 {
	if a.c != 0 && !math.IsInf(a.c, 0) && !math.IsNaN(a.c) {
		return a.s + a.c
	}
	return a.s
}

func pySum(xs []float64) float64 {
	var a pyAcc
	for _, x := range xs {
		a.add(x)
	}
	return a.value()
}

// ── 3. Prediction ───────────────────────────────────────────────────────────
type Prediction struct {
	Label         string             `json:"label"`
	Scores        map[string]float64 `json:"scores"`
	LowConfidence bool               `json:"low_confidence"`
	ModelVersion  string             `json:"model_version"`
}

// score is train.score step for step: TF counts -> x idf -> L2 normalise -> coef . x +
// intercept (sums in vocabulary order) -> softmax with the max subtracted. The label is the
// first class with the top probability; with no known word, the majority class, low confidence.
func (m *Model) score(text string) Prediction {
	counts := map[int]int{}
	for _, t := range tokenize(text, &m.Tokenizer) {
		if j, ok := m.index[t]; ok {
			counts[j]++
		}
	}
	cols := make([]int, 0, len(counts))
	for j := range counts {
		cols = append(cols, j)
	}
	sort.Ints(cols)
	weights := make([]float64, len(cols))
	var sq pyAcc
	for i, j := range cols {
		weights[i] = float64(counts[j]) * m.IDF[j]
		sq.add(weights[i] * weights[i])
	}
	norm := math.Sqrt(sq.value())
	if norm == 0 {
		norm = 1
	}
	logits := make([]float64, len(m.Classes))
	top := math.Inf(-1)
	for k, row := range m.Coef {
		var dot pyAcc
		for i, j := range cols {
			dot.add(row[j] * (weights[i] / norm))
		}
		logits[k] = m.Intercept[k] + dot.value()
		top = math.Max(top, logits[k])
	}
	exps := make([]float64, len(logits))
	for k, z := range logits {
		exps[k] = math.Exp(z - top)
	}
	total := pySum(exps)
	p := Prediction{Scores: map[string]float64{}, ModelVersion: m.Version}
	best := 0
	for k, e := range exps {
		p.Scores[m.Classes[k]] = e / total
		if e/total > exps[best]/total {
			best = k
		}
	}
	if len(cols) == 0 {
		p.Label, p.LowConfidence = m.MajorityClass, true
		return p
	}
	p.Label, p.LowConfidence = m.Classes[best], exps[best]/total < lowConfidence
	return p
}

// Classify scores text with the production model: {label, scores, low_confidence, model_version},
// the shape of ml's /predict.
func Classify(text string) (Prediction, error) {
	m, err := loadModel()
	if err != nil {
		return Prediction{}, err
	}
	return m.score(text), nil
}
