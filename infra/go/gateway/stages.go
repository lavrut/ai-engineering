// stages: the Go twin of infra/python/stages.py, the gateway's chain, one function per stage,
// run in order on every request: tag -> budget -> cache -> route -> limit -> retry/upstream.
// Sections 1-7 carry stages.py's numbers, so a lesson's goSection equals its Python section;
// 8 The chain and 9 Cases are in main.go, the body, price and JSON helpers in responses.go,
// ledger.go and json.go, as in the Python layout.
// The verdict strings, the budget maths, the token bucket and the backoff bounds are the same;
// the twin test (infra/python/tests/test_twin.py) holds both to the same lines on cases.json.
package main

import (
	"fmt"
	"math"
	"math/rand/v2"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

// ── 1. Request and state ────────────────────────────────────────────────────

type Request struct {
	Method  string
	Path    string
	Headers map[string]string // lower-cased names
	Body    []byte            // forwarded as is
	JSON    *Object           // the parsed body, nil when it is not a JSON object
}

func MakeRequest(method, path string, headers map[string]string, body []byte) *Request {
	h := map[string]string{}
	for k, v := range headers {
		h[strings.ToLower(k)] = v
	}
	return &Request{strings.ToUpper(method), path, h, body, jsonBody(body)}
}

// body is the request's JSON object, or an empty one: Python's (req.json or {}).
func (r *Request) body() *Object {
	if r.JSON == nil {
		return &Object{}
	}
	return r.JSON
}

type CacheEntry struct {
	Status     int
	Headers    map[string]string
	Body       []byte
	StopReason string // "" for none
	StoredAt   float64
	Tools      bool
}

type Bucket struct {
	Tokens   float64 // tokens left this minute
	Requests float64 // requests left this minute
	At       float64 // clock reading of the last refill
}

type State struct {
	Spend   map[string]float64     // dollars per team
	Cache   map[string]*CacheEntry // by CacheKey
	Buckets map[string]*Bucket     // by target model
	Clock   func() float64
	Rng     *rand.Rand
}

func NewState(clock func() float64, seed uint64) *State {
	return &State{map[string]float64{}, map[string]*CacheEntry{}, map[string]*Bucket{}, clock,
		rand.New(rand.NewPCG(seed, seed))}
}

type Upstream struct {
	Status  int
	Headers map[string]string
	Body    []byte
	Error   string // set when no answer came back (a dropped connection)
}

// Decision is {stage, verdict, detail} plus the keys of the stage that made it.
type Decision struct {
	Stage, Verdict, Detail string
	Tag                    string   // tag
	Team                   string   // budget: the team booked
	WorstUSD, SpentUSD     float64  // budget
	LimitUSD               *float64 // budget: nil when the team has no budget
	Key                    string   // cache
	To                     string   // route, retry
	Fallback               []string // route
	WaitMs                 int      // limit
	Attempts               int      // retry
}

type Attempt struct {
	Model     string
	Status    int // 0 for none
	Error     string
	BackoffMs int
}

type Outcome struct {
	Status    int
	Headers   map[string]string
	Body      []byte
	Decisions []Decision
	Team, Tag string
	Usage     map[string]int // nil when the answer carries none
	CostUSD   *float64
	Ms        float64
	Cached    bool
	Attempts  []Attempt
}

var jsonHeaders = map[string]string{"content-type": "application/json"}

// ── 2. Tag ──────────────────────────────────────────────────────────────────

var ticketID = regexp.MustCompile(`\bt\d{3}\b`)

// Tag: the team from x-team, or untagged. tag: x-request-tag, else the first ticket id
// (t and three digits) in the messages' text, else none.
func Tag(req *Request) Decision {
	team := req.Headers["x-team"]
	if team == "" {
		team = "untagged"
	}
	t, src := req.Headers["x-request-tag"], "x-request-tag"
	if t == "" {
		if m := ticketID.FindString(messagesText(req.body())); m != "" {
			t, src = m, "the messages"
		} else {
			t, src = "none", "nowhere"
		}
	}
	who := "x-team"
	if team == "untagged" {
		who = "no x-team header"
	}
	return Decision{Stage: "tag", Verdict: team, Detail: fmt.Sprintf("team from %s; tag %s from %s", who, t, src), Tag: t}
}

// ── 3. Budget ───────────────────────────────────────────────────────────────

// WorstCaseTokens is (input, output): about four characters a token over the canonical JSON
// (code points, as Python's len), plus one; output is max_tokens, or 0 when the request sets
// no positive integer.
func WorstCaseTokens(req *Request) (int, int) {
	body := req.body()
	out := 0
	if n, ok := body.MustGet("max_tokens").(Number); ok && n.IsInt() && n.bigInt().Sign() > 0 {
		out = int(n.Float())
	}
	return utf8.RuneCountInString(Dumps(body, true))/4 + 1, out
}

// Budget: spend so far plus this request's worst case against the team's limit: allow, warn
// (over warn_usd) or refuse (over limit_usd, or no budget for the team).
func Budget(req *Request, team string, state *State, cfg Config, prices map[string][2]float64) Decision {
	booked := team
	if team == "untagged" && cfg.Untagged != "refuse" {
		booked = cfg.Untagged
	}
	model, _ := req.body().MustGet("model").(string)
	priced := model
	if r, ok := cfg.Routes[model]; ok {
		priced = r.To
	}
	in, out := WorstCaseTokens(req)
	worst := WorstCaseUSD(priced, in, out, prices)
	spent := state.Spend[booked]
	d := Decision{Stage: "budget", Team: booked, WorstUSD: worst, SpentUSD: spent}
	b, ok := cfg.Budgets[booked]
	if !ok {
		d.Verdict, d.Detail = "refuse", "team "+booked+" has no budget"
		if booked == "untagged" {
			d.Detail = "untagged requests are refused"
		}
		return d
	}
	lim := b.LimitUSD
	d.LimitUSD = &lim
	line := fmt.Sprintf("$%.6f spent + $%.6f worst case", spent, worst)
	switch {
	case spent+worst > b.LimitUSD:
		d.Verdict, d.Detail = "refuse", fmt.Sprintf("%s > $%.2f limit", line, b.LimitUSD)
	case b.WarnUSD != nil && spent+worst > *b.WarnUSD:
		d.Verdict, d.Detail = "warn", fmt.Sprintf("%s > $%.2f warn line", line, *b.WarnUSD)
	default:
		d.Verdict, d.Detail = "allow", fmt.Sprintf("%s within $%.2f", line, b.LimitUSD)
	}
	return d
}

// ── 4. Cache ────────────────────────────────────────────────────────────────

// CacheLookup: hit (a fresh end_turn answer is stored), miss, or skip (not a Messages call, or
// it carries tools while skip_tools is on). The key hashes the path and the body bytes.
func CacheLookup(req *Request, state *State, cfg Config) Decision {
	key := CacheKey(req.Path, req.Body)
	d := Decision{Stage: "cache", Key: key}
	if req.Method != "POST" || strings.SplitN(req.Path, "?", 2)[0] != "/v1/messages" {
		d.Verdict, d.Detail = "skip", "only Messages calls are cached"
		return d
	}
	if cfg.Cache.SkipTools && truthy(req.body().MustGet("tools")) {
		d.Verdict, d.Detail = "skip", "the request carries tools"
		return d
	}
	e, ok := state.Cache[key]
	if !ok {
		d.Verdict, d.Detail = "miss", "not stored"
		return d
	}
	age := state.Clock() - e.StoredAt
	switch {
	case age >= float64(cfg.Cache.TTLs):
		delete(state.Cache, key)
		d.Verdict, d.Detail = "miss", fmt.Sprintf("expired: stored %.0fs ago, ttl %ds", age, cfg.Cache.TTLs)
	case e.StopReason != "end_turn":
		reason := e.StopReason
		if reason == "" {
			reason = "None"
		}
		d.Verdict, d.Detail = "miss", "stored answer stopped on "+reason
	default:
		d.Verdict, d.Detail = "hit", fmt.Sprintf("stored %.0fs ago", age)
	}
	return d
}

// ── 5. Route ────────────────────────────────────────────────────────────────

// Route: the body's model through routes to a target and its fallbacks; '*': deny refuses the rest.
func Route(req *Request, cfg Config) Decision {
	model, _ := req.body().MustGet("model").(string)
	if r, ok := cfg.Routes[model]; ok {
		fb := ""
		if len(r.Fallback) > 0 {
			fb = ", then " + strings.Join(r.Fallback, ", ")
		}
		return Decision{Stage: "route", Verdict: "route", Detail: model + " -> " + r.To + fb, To: r.To,
			Fallback: append([]string{}, r.Fallback...)}
	}
	if cfg.DenyUnknown {
		return Decision{Stage: "route", Verdict: "deny", Detail: model + " has no route and '*' is deny", Fallback: []string{}}
	}
	return Decision{Stage: "route", Verdict: "route", Detail: model + " has no route; sent as itself", To: model,
		Fallback: []string{}}
}

// ── 6. Limit ────────────────────────────────────────────────────────────────

// round6 is Python's round(x, 6): the correctly rounded six-place decimal, read back.
func round6(x float64) float64 {
	f, _ := strconv.ParseFloat(strconv.FormatFloat(x, 'f', 6, 64), 64)
	return f
}

// Limit: a token bucket per target model, refilled continuously at rpm and tpm per minute. A
// request takes one request and its worst-case tokens: allow now, wait (the bucket is debited now
// and the caller sleeps wait_ms, at most max_wait_ms), or refuse.
func Limit(target string, tokens int, state *State, cfg Config) Decision {
	m, ok := cfg.Models[target]
	if !ok {
		return Decision{Stage: "limit", Verdict: "allow", Detail: "no rate set for " + target}
	}
	rpm, tpm := float64(m.RPM), float64(m.TPM)
	now := state.Clock()
	b, ok := state.Buckets[target]
	if !ok {
		b = &Bucket{tpm, rpm, now}
	}
	elapsed := math.Max(0, now-b.At)
	b.Tokens = math.Min(tpm, b.Tokens+elapsed*tpm/60)
	b.Requests = math.Min(rpm, b.Requests+elapsed*rpm/60)
	b.At = now
	state.Buckets[target] = b
	if tokens > m.TPM {
		return Decision{Stage: "limit", Verdict: "refuse", Detail: fmt.Sprintf("needs %d tokens, more than tpm %d", tokens, m.TPM)}
	}
	waitS := math.Max(0, math.Max((1-b.Requests)/(rpm/60), (float64(tokens)-b.Tokens)/(tpm/60)))
	waitMs := int(math.Ceil(round6(waitS * 1000)))
	if waitMs > cfg.Limit.MaxWaitMs {
		return Decision{Stage: "limit", Verdict: "refuse", WaitMs: waitMs,
			Detail: fmt.Sprintf("needs a %d ms wait, over max_wait_ms %d", waitMs, cfg.Limit.MaxWaitMs)}
	}
	b.Requests--
	b.Tokens -= float64(tokens)
	if waitMs == 0 {
		return Decision{Stage: "limit", Verdict: "allow", Detail: fmt.Sprintf("within %d rpm and %d tpm", m.RPM, m.TPM)}
	}
	return Decision{Stage: "limit", Verdict: "wait", WaitMs: waitMs,
		Detail: fmt.Sprintf("waits %d ms for the bucket to refill", waitMs)}
}

// ── 7. Retry and upstream ───────────────────────────────────────────────────

// RetryPlan is the backoff in ms before the next try after failed attempt number `attempt`
// (from 0): exponential, capped at max_ms, plus jitter in [0, base_ms). The jitter is Go's own
// seeded generator; only its bounds are shared with Python.
func RetryPlan(cfg Config, attempt int, rng *rand.Rand) int {
	w := cfg.Retry.BaseMs
	for i := 0; i < attempt && w < cfg.Retry.MaxMs; i++ {
		w *= 2
	}
	return min(cfg.Retry.MaxMs, w) + rng.IntN(cfg.Retry.BaseMs)
}

// Send is the upstream call; Sleep waits seconds (injected so tests never wait).
type Send func(model string, req *Request) Upstream
type Sleep func(seconds float64)

// Forward tries each target in order, up to `attempts` tries each, backing off between tries on
// 429, 5xx or a dropped connection. ok, retried (answered by the route's target), fallback (by a
// later one), or fail. A 404 is not retried: from the replay proxy it means no recording.
func Forward(req *Request, targets []string, state *State, cfg Config, send Send, sleep Sleep) (*Upstream, Decision, []Attempt) {
	var attempts []Attempt
	for i, model := range targets {
		for n := 0; n < cfg.Retry.Attempts; n++ {
			up := send(model, req)
			attempts = append(attempts, Attempt{Model: model, Status: up.Status, Error: up.Error})
			if up.Error == "" && up.Status == 404 {
				return &up, Decision{Stage: "retry", Verdict: "fail", To: model, Attempts: len(attempts),
					Detail: model + " answered 404, not retried"}, attempts
			}
			if !(up.Error != "" || up.Status == 429 || up.Status >= 500) { // answered: no retry
				verdict := "fallback"
				if len(attempts) == 1 {
					verdict = "ok"
				} else if i == 0 {
					verdict = "retried"
				}
				return &up, Decision{Stage: "retry", Verdict: verdict, To: model, Attempts: len(attempts),
					Detail: fmt.Sprintf("%s answered %d on try %d", model, up.Status, len(attempts))}, attempts
			}
			if n+1 < cfg.Retry.Attempts {
				wait := max(RetryPlan(cfg, n, state.Rng), retryAfterMs(up.Headers))
				attempts[len(attempts)-1].BackoffMs = wait
				sleep(float64(wait) / 1000)
			}
		}
	}
	what := make([]string, len(attempts))
	for i, a := range attempts {
		what[i] = a.Error
		if a.Status != 0 {
			what[i] = strconv.Itoa(a.Status)
		}
	}
	return nil, Decision{Stage: "retry", Verdict: "fail", Attempts: len(attempts),
		Detail: fmt.Sprintf("%d tries over %s failed: %s", len(attempts), strings.Join(targets, ", "), strings.Join(what, ", "))}, attempts
}
