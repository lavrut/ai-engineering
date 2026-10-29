package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const fixtures = "../../data/fixtures"

// expected is one case's expect block: the decisions as stage -> verdict, and the status.
type expected struct {
	id       string
	verdicts map[string]string
	status   int
}

func loadExpected(t *testing.T) []expected {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(fixtures, "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	v, err := ParseJSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	var out []expected
	for _, c := range v.([]any) {
		o := c.(*Object)
		id, _ := o.Get("id")
		exp, _ := o.Get("expect")
		e := exp.(*Object)
		ds, _ := e.Get("decisions")
		st, _ := e.Get("status")
		x := expected{id: id.(string), verdicts: map[string]string{}, status: int(st.(Number).Float())}
		for _, d := range ds.([]any) {
			s, _ := d.(*Object).Get("stage")
			vd, _ := d.(*Object).Get("verdict")
			x.verdicts[s.(string)] = vd.(string)
		}
		out = append(out, x)
	}
	return out
}

func runAll(t *testing.T) map[string]CaseResult {
	t.Helper()
	rs, err := RunCases(filepath.Join(fixtures, "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]CaseResult{}
	for _, r := range rs {
		out[r.ID] = r
	}
	return out
}

// stageTest checks one stage's verdict on every case whose expect names that stage, and that
// a case expecting the chain to stop before it gets no decision from it.
func stageTest(t *testing.T, stage string) {
	got := runAll(t)
	seen := 0
	for _, e := range loadExpected(t) {
		t.Run(e.id, func(t *testing.T) {
			r, ok := got[e.id]
			if !ok {
				t.Fatalf("no result for %s", e.id)
			}
			var have string
			for _, d := range r.Decisions {
				if d.Stage == stage {
					have = d.Verdict
				}
			}
			if have != e.verdicts[stage] {
				t.Errorf("%s: %s verdict %q, want %q", e.id, stage, have, e.verdicts[stage])
			}
		})
		if e.verdicts[stage] != "" {
			seen++
		}
	}
	if seen == 0 {
		t.Fatalf("no case exercises %s", stage)
	}
}

func TestBudget(t *testing.T) { stageTest(t, "budget") }
func TestCache(t *testing.T)  { stageTest(t, "cache") }
func TestRoute(t *testing.T)  { stageTest(t, "route") }
func TestLimit(t *testing.T)  { stageTest(t, "limit") }
func TestRetry(t *testing.T)  { stageTest(t, "retry") }

func TestEveryCaseDecisionsAndStatus(t *testing.T) {
	got := runAll(t)
	for _, e := range loadExpected(t) {
		r := got[e.id]
		if r.Status != e.status {
			t.Errorf("%s: status %d, want %d", e.id, r.Status, e.status)
		}
		if len(r.Decisions) != len(e.verdicts) {
			t.Errorf("%s: %d decisions, want %d", e.id, len(r.Decisions), len(e.verdicts))
		}
	}
}

func TestCaseLinesAreCompactAndOrdered(t *testing.T) {
	rs, err := RunCases(filepath.Join(fixtures, "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	want := `{"id":"cacheable_hit","decisions":[{"stage":"tag","verdict":"desk"},{"stage":"budget","verdict":"allow"},{"stage":"cache","verdict":"hit"}],"status":200}`
	if got := rs[1].Line(); got != want {
		t.Errorf("line\n got %s\nwant %s", got, want)
	}
}

// ── Direct stage checks ─────────────────────────────────────────────────────

func fixtureConfig(t *testing.T) Config {
	t.Helper()
	c, err := LoadConfig(filepath.Join(fixtures, "config.json"))
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func deskRequest(t *testing.T, body string) *Request {
	t.Helper()
	v, err := ParseJSON([]byte(body))
	if err != nil {
		t.Fatal(err)
	}
	return MakeRequest("POST", "/v1/messages", map[string]string{"X-Team": "desk"}, Canonical(v))
}

const ticket = `{"model":"claude-haiku-5-5","max_tokens":1024,"messages":[{"role":"user","content":"Triage t008"}]}`

func TestWorstCaseTokensCountsCodePoints(t *testing.T) {
	r := deskRequest(t, `{"model":"é","max_tokens":10}`)
	// {"max_tokens":10,"model":"é"} is 29 code points (30 bytes): 29 // 4 + 1 = 8.
	in, out := WorstCaseTokens(r)
	if in != 8 || out != 10 {
		t.Errorf("got (%d, %d), want (8, 10)", in, out)
	}
	if _, out := WorstCaseTokens(deskRequest(t, `{"model":"m","max_tokens":10.0}`)); out != 0 {
		t.Errorf("a float max_tokens counts as none, got %d", out)
	}
}

func TestBudgetLimitAndWarnLines(t *testing.T) {
	cfg := fixtureConfig(t)
	r := deskRequest(t, ticket)
	for _, tc := range []struct {
		spent float64
		want  string
	}{{0, "allow"}, {0.85, "warn"}, {0.9999, "refuse"}} {
		s := NewState(func() float64 { return 1000 }, 0)
		s.Spend["desk"] = tc.spent
		d := Budget(r, "desk", s, cfg, LabPrices())
		if d.Verdict != tc.want {
			t.Errorf("spent %v: %s (%s), want %s", tc.spent, d.Verdict, d.Detail, tc.want)
		}
	}
	d := Budget(r, "ops", NewState(func() float64 { return 1000 }, 0), cfg, LabPrices())
	if d.Verdict != "refuse" || d.Detail != "team ops has no budget" {
		t.Errorf("got %s %q", d.Verdict, d.Detail)
	}
}

func TestCacheExpiryAndStopReason(t *testing.T) {
	cfg := fixtureConfig(t)
	r := deskRequest(t, ticket)
	s := NewState(func() float64 { return 5000 }, 0)
	key := CacheKey(r.Path, r.Body)
	s.Cache[key] = &CacheEntry{Status: 200, StopReason: "end_turn", StoredAt: 5000 - 3600}
	if d := CacheLookup(r, s, cfg); d.Verdict != "miss" || d.Detail != "expired: stored 3600s ago, ttl 3600s" {
		t.Errorf("got %s %q", d.Verdict, d.Detail)
	}
	if _, ok := s.Cache[key]; ok {
		t.Error("an expired entry is deleted")
	}
	s.Cache[key] = &CacheEntry{Status: 200, StopReason: "max_tokens", StoredAt: 4990}
	if d := CacheLookup(r, s, cfg); d.Verdict != "miss" {
		t.Errorf("a max_tokens answer is not served, got %s", d.Verdict)
	}
	get := MakeRequest("GET", "/v1/models", nil, nil)
	if d := CacheLookup(get, s, cfg); d.Verdict != "skip" {
		t.Errorf("got %s", d.Verdict)
	}
}

func TestRouteDenyAndPassThrough(t *testing.T) {
	cfg := fixtureConfig(t)
	if d := Route(deskRequest(t, `{"model":"claude-x"}`), cfg); d.Verdict != "deny" {
		t.Errorf("got %s", d.Verdict)
	}
	cfg.DenyUnknown = false
	if d := Route(deskRequest(t, `{"model":"claude-x"}`), cfg); d.Verdict != "route" || d.To != "claude-x" {
		t.Errorf("got %s to %s", d.Verdict, d.To)
	}
}

func TestLimitWaitThenRefuse(t *testing.T) {
	cfg := fixtureConfig(t)
	s := NewState(func() float64 { return 1000 }, 0)
	s.Buckets["claude-haiku-5-5"] = &Bucket{Tokens: 200000, Requests: 0, At: 1000}
	d := Limit("claude-haiku-5-5", 300, s, cfg)
	if d.Verdict != "wait" || d.WaitMs != 1000 {
		t.Errorf("got %s %d", d.Verdict, d.WaitMs)
	}
	d = Limit("claude-haiku-5-5", 300, s, cfg) // requests now at -1: a 2000 ms wait
	if d.Verdict != "refuse" || d.WaitMs != 2000 {
		t.Errorf("got %s %d", d.Verdict, d.WaitMs)
	}
	if d := Limit("claude-haiku-5-5", 200001, s, cfg); d.Verdict != "refuse" || d.WaitMs != 0 {
		t.Errorf("got %s %d", d.Verdict, d.WaitMs)
	}
	if d := Limit("unpriced", 1, s, cfg); d.Verdict != "allow" {
		t.Errorf("got %s", d.Verdict)
	}
}

func TestBackoffStaysInItsBounds(t *testing.T) {
	cfg := fixtureConfig(t) // base 200, max 2000
	s := NewState(func() float64 { return 1000 }, 7)
	for attempt := 0; attempt < 6; attempt++ {
		floor := cfg.Retry.BaseMs << attempt
		if floor > cfg.Retry.MaxMs {
			floor = cfg.Retry.MaxMs
		}
		for i := 0; i < 500; i++ {
			w := RetryPlan(cfg, attempt, s.Rng)
			if w < floor || w >= floor+cfg.Retry.BaseMs {
				t.Fatalf("attempt %d: wait %d outside [%d, %d)", attempt, w, floor, floor+cfg.Retry.BaseMs)
			}
		}
	}
}

func TestRetryNotOn404AndOkOn400(t *testing.T) {
	cfg := fixtureConfig(t)
	r := deskRequest(t, ticket)
	s := NewState(func() float64 { return 1000 }, 0)
	_, d, _ := Forward(r, []string{"claude-haiku-5-5"}, s, cfg, Scripted([]any{404}), func(float64) {})
	if d.Verdict != "fail" || d.Attempts != 1 {
		t.Errorf("got %s after %d", d.Verdict, d.Attempts)
	}
	up, d, _ := Forward(r, []string{"claude-haiku-5-5"}, s, cfg, Scripted([]any{400}), func(float64) {})
	if d.Verdict != "ok" || up.Status != 400 {
		t.Errorf("got %s %d", d.Verdict, up.Status)
	}
}

// ── Config ──────────────────────────────────────────────────────────────────

func TestLoadConfigFixture(t *testing.T) {
	c := fixtureConfig(t)
	if !c.DenyUnknown || c.Untagged != "refuse" || c.Retry.Attempts != 3 || c.Budgets["desk"].WarnUSD == nil {
		t.Errorf("unexpected config %+v", c)
	}
	if !c.Models["claude-haiku-5-5"].Lab {
		t.Error("price lab")
	}
}

func TestLoadConfigNamesTheKey(t *testing.T) {
	dir := t.TempDir()
	base, err := os.ReadFile(filepath.Join(fixtures, "config.json"))
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct{ from, to, want string }{
		{`"ttl_s": 3600`, `"ttl_s": 0`, "cache.ttl_s must be an integer above zero, got 0"},
		{`"rpm": 60`, `"rpm": 60.0`, "models.claude-haiku-5-5.rpm must be an integer above zero, got 60.0"},
		{`"retry"`, `"retries"`, "unknown key 'retries' at top level"},
		{`"warn_usd": 0.8`, `"warn_usd": 1.5`, "budgets.desk.warn_usd (1.5) is above limit_usd (1.0)"},
		{`"http://127.0.0.1:8791"`, `"https://API.example.com"`, "upstream https://API.example.com is not loopback; a remote host such as api.example.com needs live mode"},
		{`"untagged": "refuse"`, `"untagged": "ops"`, "budgets.untagged names ops, which is not a team under budgets"},
	} {
		p := filepath.Join(dir, "c.json")
		if err := os.WriteFile(p, []byte(strings.Replace(string(base), tc.from, tc.to, 1)), 0o644); err != nil {
			t.Fatal(err)
		}
		_, err := LoadConfig(p)
		if err == nil || err.Error() != tc.want {
			t.Errorf("got %v, want %q", err, tc.want)
		}
	}
}
