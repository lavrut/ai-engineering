// gateway — the Go twin of the gateway's request path (infra/python/stages.py): the same
// decisions and status on the shared fixture cases, line for line.
//
// Run (from infra/go; free and offline, the cases' upstream is a script of statuses):
//
//	go run ./gateway --cases ../data/fixtures/cases.json
//	go run ./gateway --check-config ../data/fixtures/config.json
//
// --cases prints one JSON line per case, {"id", "decisions", "status"}, as stages.py --cases
// does; the base config is the config.json beside the cases file. --check-config loads a JSON
// export (config.py --export) and exits 0, or prints the error and exits 2.
package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// ── 9. Cases and command line ───────────────────────────────────────────────

const caseClock = 1000.0

var caseReply = func() []byte {
	v, err := ParseJSON([]byte(`{"id": "msg_case", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
		"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
		"stop_sequence": null, "usage": {"input_tokens": 10, "output_tokens": 5}}`))
	if err != nil {
		panic(err)
	}
	return Canonical(v)
}()

// Canonical is responses.canonical: sorted keys, no spaces, UTF-8 unescaped.
func Canonical(v any) []byte { return []byte(Dumps(v, true)) }

// StageVerdict is the part of a decision the twins compare.
type StageVerdict struct{ Stage, Verdict string }

type CaseResult struct {
	ID        string
	id        any // as written in cases.json, for printing
	Decisions []StageVerdict
	Status    int
}

// Line is the case as stages.py prints it: keys id, decisions, status in that order, compact.
func (r CaseResult) Line() string {
	ds := make([]string, len(r.Decisions))
	for i, d := range r.Decisions {
		ds[i] = `{"stage":` + Dumps(d.Stage, false) + `,"verdict":` + Dumps(d.Verdict, false) + "}"
	}
	return `{"id":` + Dumps(r.id, false) + `,"decisions":[` + strings.Join(ds, ",") + `],"status":` + strconv.Itoa(r.Status) + "}"
}

// mergeConfig is a deep merge where overrides win: a case's config over config.json.
func mergeConfig(base, overrides *Object) *Object {
	out := base.Copy()
	for _, k := range overrides.Keys {
		v := overrides.MustGet(k)
		bo, isObj := out.MustGet(k).(*Object)
		if vo, ok := v.(*Object); ok && isObj {
			v = mergeConfig(bo, vo)
		}
		out.Set(k, v)
	}
	return out
}

// Scripted is an upstream that answers from a script: 200 (the fixed end_turn reply), 429
// (retry-after 0), another status (an error body), or "drop" (no answer).
func Scripted(script []any) Send {
	queue := append([]any{}, script...)
	return func(model string, req *Request) Upstream {
		if len(queue) == 0 {
			return Upstream{Error: "the script has no answer left"}
		}
		s := queue[0]
		queue = queue[1:]
		if s == "drop" {
			return Upstream{Error: "connection dropped"}
		}
		status := s.(int)
		if status == 200 {
			return Upstream{200, copyHeaders(jsonHeaders), caseReply, ""}
		}
		h, kind := copyHeaders(jsonHeaders), "api_error"
		if status == 429 {
			h["retry-after"], kind = "0", "rate_limit_error"
		}
		return Upstream{status, h, errorBody(kind, fmt.Sprintf("scripted %d", status)), ""}
	}
}

// caseScript turns the case's upstream list into Scripted's entries: "drop" or an int status.
func caseScript(v any) ([]any, error) {
	l, _ := v.([]any)
	out := make([]any, len(l))
	for i, s := range l {
		if n, ok := s.(Number); ok && n.IsInt() {
			out[i] = int(n.Float())
		} else if s == "drop" {
			out[i] = s
		} else {
			return nil, fmt.Errorf("upstream entry %s is neither a status nor \"drop\"", pyRepr(s))
		}
	}
	return out, nil
}

func obj(v any) *Object {
	if o, ok := v.(*Object); ok {
		return o
	}
	return &Object{}
}

func num(v any) float64 {
	if n, ok := v.(Number); ok {
		return n.Float()
	}
	return 0
}

// caseState is a case's state at caseClock: spend per team; stored cache entries (key "self" is
// this case's own request), each with stop_reason, tools flag and age_s; bucket levels per model.
func caseState(req *Request, st *Object) *State {
	s := NewState(func() float64 { return caseClock }, 0)
	sp := obj(st.MustGet("spend"))
	for _, team := range sp.Keys {
		s.Spend[team] = num(sp.MustGet(team))
	}
	entries, _ := st.MustGet("cache").([]any)
	for _, e := range entries {
		eo := obj(e)
		key, _ := eo.MustGet("key").(string)
		if key == "self" {
			key = CacheKey(req.Path, req.Body)
		}
		reason, _ := eo.MustGet("stop_reason").(string)
		s.Cache[key] = &CacheEntry{200, copyHeaders(jsonHeaders), caseReply, reason,
			caseClock - num(eo.MustGet("age_s")), truthy(eo.MustGet("tools"))}
	}
	bk := obj(st.MustGet("bucket"))
	for _, model := range bk.Keys {
		b := obj(bk.MustGet(model))
		s.Buckets[model] = &Bucket{num(b.MustGet("tokens")), num(b.MustGet("requests")), caseClock}
	}
	return s
}

func runCase(c *Object, base *Object) (CaseResult, error) {
	id := c.MustGet("id")
	cfg, err := FromExport(mergeConfig(base, obj(c.MustGet("config"))), false)
	if err != nil {
		return CaseResult{}, fmt.Errorf("case %s: %w", pyStr(id), err)
	}
	r := obj(c.MustGet("request"))
	headers := map[string]string{}
	h := obj(r.MustGet("headers"))
	for _, k := range h.Keys {
		headers[k] = pyStr(h.MustGet(k))
	}
	method, _ := r.MustGet("method").(string)
	path, _ := r.MustGet("path").(string)
	req := MakeRequest(method, path, headers, Canonical(r.MustGet("body")))
	script, err := caseScript(c.MustGet("upstream"))
	if err != nil {
		return CaseResult{}, fmt.Errorf("case %s: %w", pyStr(id), err)
	}
	out := RunChain(req, caseState(req, obj(c.MustGet("state"))), cfg, Scripted(script), func(float64) {})
	res := CaseResult{ID: pyStr(id), id: id, Status: out.Status}
	for _, d := range out.Decisions {
		res.Decisions = append(res.Decisions, StageVerdict{d.Stage, d.Verdict})
	}
	return res, nil
}

func readJSON(path string) (any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	v, err := ParseJSON(raw)
	if err != nil {
		return nil, fmt.Errorf("%s: not valid JSON (%v)", path, err)
	}
	return v, nil
}

// RunCases runs every case in a cases.json over the config.json beside it.
func RunCases(path string) ([]CaseResult, error) {
	b, err := readJSON(filepath.Join(filepath.Dir(path), "config.json"))
	if err != nil {
		return nil, err
	}
	base, ok := b.(*Object)
	if !ok {
		return nil, errors.New("config.json is not a JSON object")
	}
	v, err := readJSON(path)
	if err != nil {
		return nil, err
	}
	cases, ok := v.([]any)
	if !ok {
		return nil, fmt.Errorf("%s is not a JSON list", path)
	}
	var out []CaseResult
	for _, c := range cases {
		r, err := runCase(obj(c), base)
		if err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, nil
}

func run(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("gateway", flag.ContinueOnError)
	fs.SetOutput(stderr)
	cases := fs.String("cases", "", "a cases.json; prints one JSON line per case")
	check := fs.String("check-config", "", "a JSON config export; exit 0 if it loads, else 2")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	switch {
	case *check != "":
		c, err := LoadConfig(*check)
		if err != nil {
			fmt.Fprintf(stderr, "config error: %v\n", err)
			return 2
		}
		fmt.Fprintf(stdout, "ok: %d model(s), %d route(s), %d budget(s)\n", len(c.Models), len(c.Routes), len(c.Budgets))
		return 0
	case *cases != "":
		rs, err := RunCases(*cases)
		if err != nil {
			fmt.Fprintln(stderr, err)
			return 1
		}
		for _, r := range rs {
			fmt.Fprintln(stdout, r.Line())
		}
		return 0
	}
	fmt.Fprintln(stderr, "usage: gateway --cases PATH | --check-config PATH")
	return 2
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}
