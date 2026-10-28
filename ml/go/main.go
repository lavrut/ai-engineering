// ml-serve — the production complaint classifier in Go, from the exported model JSON alone.
//
// The same scores as Python's train.score (to 1e-9), the same HTTP contract as
// ml/serve/server.py, and the same PSI drift numbers as monitor.py (to 1e-9).
//
// Run (from the repo root; or `go run .` inside ml/go). The ml/ directory (registry.json,
// models/) is found from this source file's location, so a built binary works from any
// directory on this machine; ML_ROOT overrides it (e.g. for a binary copied elsewhere).
//
//	go -C ml/go run . serve -port 8001
//	go -C ml/go run . predict "my mortgage payment was misapplied"
//	go -C ml/go run . drift          (section 6 lives in drift.go)
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

// ── 1. Configuration ────────────────────────────────────────────────────────
const (
	maxChars      = 20000  // text length limit, in code points (runes)
	maxBody       = 131072 // bytes: 20,000 code points as \uXXXX escapes, plus overhead
	lowConfidence = 0.5    // below this top probability, a prediction is flagged
	tokenPattern  = "[a-z0-9']+"
	shownWords    = 5 // top shifted words per period; drift.json doesn't store it (monitor.py SHOWN_WORDS)
)

var (
	lengthError = map[string]string{"error": "text must be 1 to 20000 characters"}
	bodyError   = map[string]string{"error": `body must be a JSON object with a string field "text"`}
	splits      = [][3]string{ // name, first day, last day (inclusive), as train.SPLITS
		{"train", "2019-01-01", "2021-12-31"},
		{"test", "2022-01-01", "2022-12-31"},
		{"stream", "2023-01-01", "2024-12-31"},
	}
)

// mlRoot is where registry.json and models/ live: ML_ROOT if set, else the ml/ directory
// next to this source file, so the commands work from any working directory.
func mlRoot() string {
	if r := os.Getenv("ML_ROOT"); r != "" {
		return r
	}
	_, file, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(file), "..")
}

// ── 2. Model loading ────────────────────────────────────────────────────────
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

// ── 3. Tokenizer and TF-IDF ─────────────────────────────────────────────────
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

// ── 4. Prediction ───────────────────────────────────────────────────────────
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

// ── 5. HTTP server ──────────────────────────────────────────────────────────
func writeJSON(w http.ResponseWriter, status int, v any) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false) // like Python's json: "<" stays "<"
	enc.Encode(v)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(bytes.TrimSuffix(buf.Bytes(), []byte("\n")))
}

// readText returns the "text" field, or false when the body is malformed: not strict UTF-8
// (a BOM counts as malformed), not a JSON object, or no string "text". A map, not a struct:
// Go matches struct fields case-insensitively ("TEXT" would pass), and with a map of raw
// values the last duplicate key wins and other fields are never converted. encoding/json is
// strict like server.py: NaN / Infinity and nesting deeper than it parses are malformed (400).
// A lone surrogate escape ("\ud800") becomes U+FFFD: 1 code point and a separator, as in Python.
func readText(body []byte) (string, bool) {
	if !utf8.Valid(body) || bytes.HasPrefix(body, []byte("\xef\xbb\xbf")) {
		return "", false
	}
	var obj map[string]json.RawMessage
	if json.Unmarshal(body, &obj) != nil {
		return "", false
	}
	raw := obj["text"]
	var text string
	if len(raw) == 0 || raw[0] != '"' || json.Unmarshal(raw, &text) != nil {
		return "", false
	}
	return text, true
}

func newHandler(m *Model) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, struct { // a struct keeps Python's key order; a map would sort them
			Status       string `json:"status"`
			ModelVersion string `json:"model_version"`
		}{"ok", m.Version})
	})
	mux.HandleFunc("POST /predict", func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBody)) // Content-Length not trusted
		var tooBig *http.MaxBytesError
		if errors.As(err, &tooBig) {
			writeJSON(w, 400, lengthError)
			return
		}
		text, ok := readText(body)
		if err != nil || !ok {
			writeJSON(w, 400, bodyError)
			return
		}
		if n := utf8.RuneCountInString(text); n < 1 || n > maxChars { // runes = Python's len
			writeJSON(w, 400, lengthError)
			return
		}
		writeJSON(w, 200, m.score(text))
	})
	return mux // unknown routes and wrong methods: Go's defaults, outside the contract
}

// ── 7. Entry point ──────────────────────────────────────────────────────────
func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: ml-serve serve [-port N] | predict TEXT | drift")
		os.Exit(2)
	}
	root := mlRoot()
	m, err := loadProduction(root)
	if err != nil {
		fmt.Fprintln(os.Stderr, "startup failed:", err)
		os.Exit(1)
	}
	switch os.Args[1] {
	case "serve":
		fs := flag.NewFlagSet("serve", flag.ExitOnError)
		port := fs.Int("port", 8001, "port on 127.0.0.1")
		fs.Parse(os.Args[2:])
		addr := fmt.Sprintf("127.0.0.1:%d", *port)
		log.Printf("serving %s on http://%s", m.Version, addr)
		srv := &http.Server{Addr: addr, Handler: newHandler(m), ReadHeaderTimeout: 10 * time.Second,
			ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second}
		log.Fatal(srv.ListenAndServe())
	case "predict": // scoring only: no length limit, as in train.score
		out, _ := json.MarshalIndent(m.score(strings.Join(os.Args[2:], " ")), "", "  ")
		fmt.Println(string(out))
	case "drift":
		rep, err := computeDrift(root, m)
		if err != nil {
			fmt.Fprintln(os.Stderr, "drift:", err)
			os.Exit(1)
		}
		printDrift(rep)
	default:
		fmt.Fprintf(os.Stderr, "unknown command %q\n", os.Args[1])
		os.Exit(2)
	}
}
