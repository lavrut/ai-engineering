// Package core is the shared core behind both Lumen MCP servers; it matches lumen_core (Python).
//
// Ticket text is untrusted data: it only ever leaves this package inside UntrustedBody.
package core

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
)

// ── 1. Constants and root ───────────────────────────────────────────────────
var Categories = []string{"billing", "bug", "account", "how_to", "feature_request", "outage"}

const (
	MaxQueryChars = 200
	MinLimit      = 1
	MaxLimit      = 20
	DefaultLimit  = 5
	MaxTextChars  = 20000
	MaxBodyBytes  = 131072
)

// Root is the repo root: MCP_ROOT if set, else found from this source file
// (mcp/go/core/tickets.go, three levels down), so it works from any working directory.
func Root() string {
	if r := os.Getenv("MCP_ROOT"); r != "" {
		return r
	}
	_, file, _, _ := runtime.Caller(0)
	return filepath.Clean(filepath.Join(filepath.Dir(file), "..", "..", ".."))
}

// LabelsPath is the git-ignored label store.
func LabelsPath() string { return filepath.Join(Root(), "mcp", "data", "labels.jsonl") }

// ── 2. Loading ──────────────────────────────────────────────────────────────
type Ticket struct {
	ID       string `json:"id"`
	Text     string `json:"text"`
	Category string `json:"category"`
	Priority string `json:"priority"`
}

// Hit is what leaves the package. The body is data under one key, never merged into structure.
type Hit struct {
	ID            string `json:"id"`
	Category      string `json:"category"`
	Priority      string `json:"priority"`
	UntrustedBody string `json:"untrusted_body"`
}

func hit(t Ticket) Hit { return Hit{t.ID, t.Category, t.Priority, t.Text} }

// LoadTickets returns all tickets in file order: t001-t060 from the lab, then t061.
func LoadTickets() ([]Ticket, error) {
	var rows []Ticket
	for _, p := range []string{
		filepath.Join(Root(), "llms", "data", "tickets.jsonl"),
		filepath.Join(Root(), "mcp", "data", "injection.jsonl"),
	} {
		f, err := os.Open(p)
		if err != nil {
			return nil, err
		}
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1<<20), 1<<24)
		for sc.Scan() {
			if strings.TrimSpace(sc.Text()) == "" {
				continue
			}
			var t Ticket
			if err := json.Unmarshal(sc.Bytes(), &t); err != nil {
				f.Close()
				return nil, fmt.Errorf("%s: %w", p, err)
			}
			rows = append(rows, t)
		}
		f.Close()
		if err := sc.Err(); err != nil {
			return nil, err
		}
	}
	return rows, nil
}

// ── 3. Search and get ───────────────────────────────────────────────────────
// asciiLower lowercases A-Z only, like Python's ascii_lower. strings.ToLower would fold
// non-ASCII letters (İ, Σ) and the contract pins that it must not.
func asciiLower(s string) string {
	b := []byte(s)
	for i, c := range b {
		if 'A' <= c && c <= 'Z' {
			b[i] = c + 'a' - 'A'
		}
	}
	return string(b)
}

// asciiFields splits on space, \t, \n, \r, \v, \f only (strings.Fields also splits on nbsp, U+0085...).
func asciiFields(s string) []string {
	return strings.FieldsFunc(s, func(r rune) bool {
		switch r {
		case ' ', '\t', '\n', '\r', '\v', '\f':
			return true
		}
		return false
	})
}

// Search: ASCII-case-insensitive substring match per query word; score = number of words that
// hit. Ties break by id ascending. Tickets with no hit are left out.
func Search(query string, limit int) ([]Hit, error) {
	ts, err := LoadTickets()
	if err != nil {
		return nil, err
	}
	words := asciiFields(asciiLower(query))
	type scored struct {
		n int
		t Ticket
	}
	var ss []scored
	for _, t := range ts {
		text := asciiLower(t.Text)
		n := 0
		for _, w := range words {
			if strings.Contains(text, w) {
				n++
			}
		}
		if n > 0 {
			ss = append(ss, scored{n, t})
		}
	}
	sort.SliceStable(ss, func(i, j int) bool {
		if ss[i].n != ss[j].n {
			return ss[i].n > ss[j].n
		}
		return ss[i].t.ID < ss[j].t.ID
	})
	if limit < 0 {
		limit = 0
	}
	if len(ss) > limit {
		ss = ss[:limit]
	}
	out := make([]Hit, 0, len(ss))
	for _, s := range ss {
		out = append(out, hit(s.t))
	}
	return out, nil
}

// Get returns one ticket by id; the error is "unknown ticket: {id}" when unknown.
func Get(id string) (Hit, error) {
	ts, err := LoadTickets()
	if err != nil {
		return Hit{}, err
	}
	for _, t := range ts {
		if t.ID == id {
			return hit(t), nil
		}
	}
	return Hit{}, fmt.Errorf("unknown ticket: %s", id)
}
