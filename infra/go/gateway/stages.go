// stages: the Go twin of infra/python/stages.py, the gateway's chain, one function per stage,
// run in order on every request: tag -> budget -> cache -> route -> limit -> retry/upstream.
// The sections carry stages.py's numbers, so a lesson's goSection equals its Python section.
// The verdict strings, the budget maths, the token bucket and the backoff bounds are the same;
// the twin test (infra/python/tests/test_twin.py) holds both to the same lines on cases.json.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
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

// messagesText is the messages' text in order: string contents and text blocks.
func messagesText(body *Object) string {
	var parts []string
	ms, _ := body.MustGet("messages").([]any)
	for _, m := range ms {
		mo, ok := m.(*Object)
		if !ok {
			continue
		}
		switch c := mo.MustGet("content").(type) {
		case string:
			parts = append(parts, c)
		case []any:
			for _, b := range c {
				if bo, ok := b.(*Object); ok {
					if s, ok := bo.MustGet("text").(string); ok {
						parts = append(parts, s)
					}
				}
			}
		}
	}
	return strings.Join(parts, "\n")
}

// ── 3. Budget ───────────────────────────────────────────────────────────────

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

// WorstCaseUSD is ledger.worst_case_usd: no caching, every output token; 0 for an unpriced model.
func WorstCaseUSD(model string, in, out int, prices map[string][2]float64) float64 {
	p, ok := prices[model]
	if !ok {
		return 0
	}
	return (float64(in)*p[0] + float64(out)*p[1]) / 1_000_000
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

// CacheKey is the sha256 hex of the path, a newline and the body bytes as received.
func CacheKey(path string, body []byte) string {
	h := sha256.New()
	h.Write([]byte(path + "\n"))
	h.Write(body)
	return hex.EncodeToString(h.Sum(nil))
}

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

// retryAfterMs is an answer's retry-after (seconds) in ms; 0 when absent or unreadable.
func retryAfterMs(headers map[string]string) int {
	for k, v := range headers {
		if strings.ToLower(k) == "retry-after" {
			f, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
			if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
				return 0
			}
			return max(0, int(f*1000))
		}
	}
	return 0
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

// ── 8. The chain ────────────────────────────────────────────────────────────

var usageFields = []string{"input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"}

func jsonBody(body []byte) *Object {
	v, err := ParseJSON(body)
	if err != nil {
		return nil
	}
	o, _ := v.(*Object)
	return o
}

func errorBody(kind, message string) []byte {
	b, _ := json.Marshal(map[string]any{"type": "error", "error": map[string]string{"type": kind, "message": message}})
	return b
}

// responseUsage is the four usage fields of a JSON answer (a missing one is 0), or nil. The
// twin reads JSON answers only: the fixture cases never stream.
func responseUsage(body []byte) map[string]int {
	d := jsonBody(body)
	if d == nil {
		return nil
	}
	u, ok := d.MustGet("usage").(*Object)
	if !ok {
		return nil
	}
	out := map[string]int{}
	for _, k := range usageFields {
		if n, ok := u.MustGet(k).(Number); ok {
			out[k] = int(n.Float())
		} else {
			out[k] = 0
		}
	}
	return out
}

func stopReason(body []byte) string {
	if d := jsonBody(body); d != nil {
		s, _ := d.MustGet("stop_reason").(string)
		return s
	}
	return ""
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

// RunChain: parse -> tag -> budget -> cache -> route -> limit -> retry/upstream -> usage and cost.
// The first refusal stops it: 400 parse, 402 budget, 404 deny, 429 limit, 502 upstream.
func RunChain(req *Request, state *State, cfg Config, send Send, sleep Sleep) Outcome {
	t0 := state.Clock()
	out := Outcome{Status: 500, Headers: copyHeaders(jsonHeaders), Team: "untagged", Tag: "none"}
	finish := func(status int, body []byte, headers map[string]string) Outcome {
		out.Status, out.Body = status, body
		for k, v := range headers {
			out.Headers[k] = v
		}
		out.Ms = (state.Clock() - t0) * 1000
		return out
	}

	body := req.JSON
	if _, ok := body.getString("model"); !ok {
		why := "the body names no model"
		if body == nil {
			why = "the body is not a JSON object"
		}
		out.Decisions = append(out.Decisions, Decision{Stage: "parse", Verdict: "refuse", Detail: why})
		return finish(400, errorBody("invalid_request_error", why), nil)
	}

	d := Tag(req)
	out.Decisions = append(out.Decisions, d)
	out.Team, out.Tag = d.Verdict, d.Tag

	prices, err := ResolvePrices(cfg)
	if err != nil { // Python's resolve_prices raises inside the budget stage: a 500 naming it
		out.Decisions = append(out.Decisions, Decision{Stage: "budget", Verdict: "error", Detail: "ValueError"})
		return finish(500, errorBody("api_error", "gateway stage budget failed: ValueError"), nil)
	}
	d = Budget(req, out.Team, state, cfg, prices)
	out.Decisions = append(out.Decisions, d)
	booked := d.Team
	if d.Verdict == "refuse" {
		lim := ""
		if d.LimitUSD != nil {
			lim = fmt.Sprintf(" of $%.2f", *d.LimitUSD)
		}
		return finish(402, errorBody("budget_exceeded", fmt.Sprintf("team %s: budget%s refused: %s", booked, lim, d.Detail)), nil)
	}

	d = CacheLookup(req, state, cfg)
	out.Decisions = append(out.Decisions, d)
	key, cacheVerdict := d.Key, d.Verdict
	if cacheVerdict == "hit" {
		e := state.Cache[key]
		zero := 0.0
		out.Cached, out.Usage, out.CostUSD = true, responseUsage(e.Body), &zero
		h := copyHeaders(e.Headers)
		h["x-cache"] = "hit"
		return finish(e.Status, e.Body, h)
	}

	r := Route(req, cfg)
	out.Decisions = append(out.Decisions, r)
	if r.Verdict == "deny" {
		return finish(404, errorBody("not_found_error", r.Detail), nil)
	}

	in, outTok := WorstCaseTokens(req)
	d = Limit(r.To, in+outTok, state, cfg)
	out.Decisions = append(out.Decisions, d)
	if d.Verdict == "refuse" {
		after := strconv.Itoa(max(1, int(math.Ceil(float64(d.WaitMs)/1000))))
		return finish(429, errorBody("rate_limit_error", d.Detail), map[string]string{"retry-after": after})
	}
	if d.Verdict == "wait" {
		sleep(float64(d.WaitMs) / 1000)
	}

	up, d, attempts := Forward(req, append([]string{r.To}, r.Fallback...), state, cfg, send, sleep)
	out.Attempts = attempts
	out.Decisions = append(out.Decisions, d)
	if d.Verdict == "fail" {
		said := ""
		if up != nil {
			s := strings.ToValidUTF8(string(up.Body), "�")
			if utf8.RuneCountInString(s) > 500 {
				s = string([]rune(s)[:500])
			}
			said = ": " + s
		}
		return finish(502, errorBody("upstream_error", d.Detail+said), nil)
	}

	out.Usage = responseUsage(up.Body)
	if out.Usage != nil {
		out.CostUSD = costOf(d.To, out.Usage, prices)
	}
	if out.CostUSD != nil {
		state.Spend[booked] += *out.CostUSD
	}
	if cacheVerdict == "miss" && up.Status == 200 && stopReason(up.Body) == "end_turn" {
		state.Cache[key] = &CacheEntry{200, copyHeaders(up.Headers), up.Body, "end_turn", state.Clock(), false}
	}
	h := copyHeaders(up.Headers)
	h["x-cache"] = cacheVerdict
	out.Headers = map[string]string{}
	return finish(up.Status, up.Body, h)
}

func copyHeaders(h map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range h {
		out[k] = v
	}
	return out
}

func (o *Object) getString(k string) (string, bool) {
	if o == nil {
		return "", false
	}
	s, ok := o.MustGet(k).(string)
	return s, ok
}
