// config: the gateway config, read from the JSON export (infra/data/fixtures/config.json, written
// by infra/python/config.py --export). Go never parses YAML. The rules are config.py's, with the
// same error wording for the key; the upstream must be loopback (the twin has no live mode).
package main

import (
	"fmt"
	"math"
	"os"
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"
)

// ── 1. Shape ────────────────────────────────────────────────────────────────

var sections = []string{"upstream", "models", "routes", "cache", "retry", "limit", "budgets", "traces"}
var loopback = []string{"127.0.0.1", "localhost", "::1"}

// ConfigError is a malformed config; the message names the key.
type ConfigError string

func (e ConfigError) Error() string { return string(e) }

func cerr(format string, a ...any) error { return ConfigError(fmt.Sprintf(format, a...)) }

type ModelCfg struct {
	RPM, TPM int
	Lab      bool       // price is the literal "lab": the LLMs lab's table
	Price    [2]float64 // (input, output) dollars per million tokens, when not Lab
}

type RouteCfg struct {
	To       string
	Fallback []string
}

type CacheCfg struct {
	TTLs      int
	SkipTools bool
}

type RetryCfg struct{ Attempts, BaseMs, MaxMs int }

type LimitCfg struct{ MaxWaitMs int }

type BudgetCfg struct {
	LimitUSD float64
	WarnUSD  *float64
}

type Config struct {
	Upstream    string
	Models      map[string]ModelCfg
	Routes      map[string]RouteCfg
	DenyUnknown bool
	Cache       CacheCfg
	Retry       RetryCfg
	Limit       LimitCfg
	Budgets     map[string]BudgetCfg
	Untagged    string // "refuse", or the team untagged requests are booked to
	TracesDir   string
}

// ── 2. Rules ────────────────────────────────────────────────────────────────

// hostname is urllib.parse.urlsplit(url).hostname: the netloc after "//", past any "user@",
// without the port, bracketless for IPv6, lower-cased; "" for none (Python's None).
func hostname(url string) (string, bool) {
	rest := url
	if i := strings.IndexByte(rest, ':'); i > 0 && isScheme(rest[:i]) {
		rest = rest[i+1:]
	}
	if !strings.HasPrefix(rest, "//") {
		return "", true
	}
	netloc := rest[2:]
	if i := strings.IndexAny(netloc, "/?#"); i >= 0 {
		netloc = netloc[:i]
	}
	if strings.Contains(netloc, "[") != strings.Contains(netloc, "]") {
		return "", false // urlsplit raises ValueError: Invalid IPv6 URL
	}
	if i := strings.LastIndexByte(netloc, '@'); i >= 0 {
		netloc = netloc[i+1:]
	}
	host := netloc
	if i := strings.IndexByte(netloc, '['); i >= 0 {
		host = netloc[i+1:]
		if j := strings.IndexByte(host, ']'); j >= 0 {
			host = host[:j]
		}
	} else if i := strings.IndexByte(netloc, ':'); i >= 0 {
		host = netloc[:i]
	}
	return strings.ToLower(host), true
}

func isScheme(s string) bool {
	for i, r := range s {
		ok := r < utf8.RuneSelf && (unicode.IsLetter(r) || (i > 0 && (unicode.IsDigit(r) || strings.ContainsRune("+-.", r))))
		if !ok {
			return false
		}
	}
	return s != ""
}

func isLoopback(url string) bool {
	h, ok := hostname(url)
	return ok && slices.Contains(loopback, h)
}

// rules applies config.py's checks in its order, keeping the first error: once one is set,
// every later check does nothing, so the message is the one Python raises first.
type rules struct{ err error }

func (r *rules) fail(format string, a ...any) {
	if r.err == nil {
		r.err = cerr(format, a...)
	}
}

// mapping is _map: a mapping with only the known keys and every required one; nil on error.
func (r *rules) mapping(x any, where string, keys []string, optional ...string) *Object {
	o, ok := x.(*Object)
	if r.err != nil || !ok {
		r.fail("%s must be a mapping", where)
		return nil
	}
	for _, k := range o.Keys {
		if !slices.Contains(keys, k) && !slices.Contains(optional, k) {
			r.fail("unknown key %s in %s", pyRepr(k), where)
		}
	}
	for _, k := range keys {
		if _, ok := o.Get(k); !ok {
			r.fail("%s.%s is missing", where, k)
		}
	}
	return o
}

// number is _num: a finite number above zero.
func (r *rules) number(x any, where string) float64 {
	n, ok := x.(Number)
	f := 0.0
	if ok {
		f = n.Float()
	}
	if !ok || math.IsInf(f, 0) || math.IsNaN(f) || f <= 0 {
		r.fail("%s must be a finite number above zero, got %s", where, pyRepr(x))
	}
	return f
}

// integer is _int: an integer literal above zero (true and 60.0 are not).
func (r *rules) integer(x any, where string) int {
	n, ok := x.(Number)
	if !ok || !n.IsInt() || n.bigInt().Sign() <= 0 {
		r.fail("%s must be an integer above zero, got %s", where, pyRepr(x))
		return 0
	}
	if !n.bigInt().IsInt64() || n.bigInt().Int64() > math.MaxInt32 {
		return math.MaxInt32 // Python's ints have no ceiling; ours stops well past any rate
	}
	return int(n.bigInt().Int64())
}

// name is _name: a non-empty string.
func (r *rules) name(x any, where string) string {
	s, ok := x.(string)
	if !ok || s == "" {
		r.fail("%s must be a non-empty string", where)
	}
	return s
}

// price is _price: the literal "lab", or [input, output] per million tokens.
func (r *rules) price(x any, where string, m *ModelCfg) {
	if x == "lab" {
		m.Lab = true
		return
	}
	if l, ok := x.([]any); ok && len(l) == 2 {
		m.Price = [2]float64{r.number(l[0], where+"[0]"), r.number(l[1], where+"[1]")}
		return
	}
	r.fail("%s must be 'lab' or [input, output] per million tokens", where)
}

// ── 3. Load ─────────────────────────────────────────────────────────────────

// LoadConfig reads a JSON export and applies the rules; the upstream must be loopback.
func LoadConfig(path string) (Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Config{}, err
	}
	v, err := ParseJSON(raw)
	if err != nil {
		return Config{}, fmt.Errorf("%s: not valid JSON (%v)", path, err)
	}
	return FromExport(v, false)
}

// build is every rule over the YAML's shape, in config.py's order.
func build(raw *Object, live bool) (Config, error) {
	var c Config
	r := &rules{}
	for _, k := range raw.Keys {
		if !slices.Contains(sections, k) {
			r.fail("unknown key %s at top level", pyRepr(k))
		}
	}
	for _, k := range sections {
		if _, ok := raw.Get(k); !ok {
			r.fail("missing section: %s", k)
		}
	}
	if c.Upstream = r.name(raw.MustGet("upstream"), "upstream"); r.err == nil && !live && !isLoopback(c.Upstream) {
		h, ok := hostname(c.Upstream)
		if !ok {
			return c, fmt.Errorf("upstream %s: Invalid IPv6 URL", c.Upstream) // urlsplit raises (not a ConfigError)
		}
		if h == "" {
			h = "None"
		}
		r.fail("upstream %s is not loopback; a remote host such as %s needs live mode", c.Upstream, h)
	}

	c.Models = map[string]ModelCfg{}
	if mo, ok := raw.MustGet("models").(*Object); !ok || len(mo.Keys) == 0 {
		r.fail("models must be a non-empty mapping")
	} else {
		for _, n := range mo.Keys {
			w := "models." + n
			m := r.mapping(mo.MustGet(n), w, []string{"rpm", "tpm", "price"})
			mc := ModelCfg{RPM: r.integer(m.MustGet("rpm"), w+".rpm"), TPM: r.integer(m.MustGet("tpm"), w+".tpm")}
			r.price(m.MustGet("price"), w+".price", &mc)
			c.Models[n] = mc
		}
	}

	c.Routes = map[string]RouteCfg{}
	ro, ok := raw.MustGet("routes").(*Object)
	if !ok {
		r.fail("routes must be a mapping")
	}
	for _, n := range ro.keys() {
		v := ro.MustGet(n)
		if n == "*" {
			if v != "deny" {
				r.fail("routes.'*' must be deny")
			}
			c.DenyUnknown = true
			continue
		}
		w := "routes." + n
		rt := r.mapping(v, w, []string{"to"}, "fallback")
		fb, ok := rt.Get("fallback")
		if !ok {
			fb = []any{}
		}
		fl, ok := fb.([]any)
		if !ok {
			r.fail("%s.fallback must be a list", w)
		}
		rc := RouteCfg{Fallback: []string{}}
		for i, t := range append([]any{rt.MustGet("to")}, fl...) {
			s, isStr := t.(string)
			if _, known := c.Models[s]; !isStr || !known {
				r.fail("%s names %s, which is not under models", w, pyStr(t))
			}
			if i == 0 {
				rc.To = s
			} else {
				rc.Fallback = append(rc.Fallback, s)
			}
		}
		c.Routes[n] = rc
	}

	cm := r.mapping(raw.MustGet("cache"), "cache", []string{"ttl_s", "skip_tools"})
	st, ok := cm.MustGet("skip_tools").(bool)
	if !ok {
		r.fail("cache.skip_tools must be true or false")
	}
	c.Cache = CacheCfg{r.integer(cm.MustGet("ttl_s"), "cache.ttl_s"), st}

	rm := r.mapping(raw.MustGet("retry"), "retry", []string{"attempts", "base_ms", "max_ms"})
	c.Retry = RetryCfg{r.integer(rm.MustGet("attempts"), "retry.attempts"), r.integer(rm.MustGet("base_ms"), "retry.base_ms"),
		r.integer(rm.MustGet("max_ms"), "retry.max_ms")}
	if c.Retry.MaxMs < c.Retry.BaseMs {
		r.fail("retry.max_ms must not be below retry.base_ms")
	}

	lm := r.mapping(raw.MustGet("limit"), "limit", []string{"max_wait_ms"})
	c.Limit.MaxWaitMs = r.integer(lm.MustGet("max_wait_ms"), "limit.max_wait_ms")

	c.Budgets = map[string]BudgetCfg{}
	bo, ok := raw.MustGet("budgets").(*Object)
	if !ok {
		r.fail("budgets must be a mapping")
	}
	haveUntagged := false
	for _, team := range bo.keys() {
		if team == "untagged" {
			c.Untagged, haveUntagged = r.name(bo.MustGet(team), "budgets.untagged"), true
			continue
		}
		w := "budgets." + team
		b := r.mapping(bo.MustGet(team), w, []string{"limit_usd"}, "warn_usd")
		bc := BudgetCfg{LimitUSD: r.number(b.MustGet("limit_usd"), w+".limit_usd")}
		if x, ok := b.Get("warn_usd"); ok {
			warn := r.number(x, w+".warn_usd")
			if warn > bc.LimitUSD {
				r.fail("%s.warn_usd (%s) is above limit_usd (%s)", w, floatRepr(warn), floatRepr(bc.LimitUSD))
			}
			bc.WarnUSD = &warn
		}
		c.Budgets[team] = bc
	}
	if !haveUntagged {
		r.fail("budgets.untagged is missing (refuse, or a team name)")
	}
	if _, ok := c.Budgets[c.Untagged]; c.Untagged != "refuse" && !ok {
		r.fail("budgets.untagged names %s, which is not a team under budgets", c.Untagged)
	}

	tm := r.mapping(raw.MustGet("traces"), "traces", []string{"dir"})
	c.TracesDir = r.name(tm.MustGet("dir"), "traces.dir")
	return c, r.err
}

// ── 4. The export's shape ───────────────────────────────────────────────────

// FromExport is config.from_dict: the export's shape (config.py's to_json) turned back into
// the YAML's, then build.
func FromExport(d any, live bool) (Config, error) {
	o, ok := d.(*Object)
	if !ok {
		return Config{}, cerr("config must be a mapping")
	}
	top := []string{"upstream", "models", "routes", "deny_unknown", "cache", "retry", "limit", "budgets", "untagged", "traces_dir"}
	for _, k := range o.Keys {
		if !slices.Contains(top, k) {
			return Config{}, cerr("unknown key %s at top level", pyRepr(k))
		}
	}
	raw := &Object{}
	for _, k := range []string{"upstream", "models", "cache", "retry", "limit"} {
		if v, ok := o.Get(k); ok {
			raw.Set(k, v)
		}
	}
	if ro, ok := o.MustGet("routes").(*Object); ok {
		routes := ro.Copy()
		if truthy(o.MustGet("deny_unknown")) {
			routes.Set("*", "deny")
		}
		raw.Set("routes", routes)
	}
	if bo, ok := o.MustGet("budgets").(*Object); ok {
		budgets := &Object{}
		for _, t := range bo.Keys {
			v := bo.MustGet(t)
			if team, ok := v.(*Object); ok {
				kept := &Object{}
				for _, k := range team.Keys {
					if x := team.MustGet(k); !(k == "warn_usd" && x == nil) {
						kept.Set(k, x)
					}
				}
				v = kept
			}
			budgets.Set(t, v)
		}
		if u, ok := o.Get("untagged"); ok {
			budgets.Set("untagged", u)
		}
		raw.Set("budgets", budgets)
	}
	if t, ok := o.Get("traces_dir"); ok {
		raw.Set("traces", &Object{Keys: []string{"dir"}, vals: map[string]any{"dir": t}})
	}
	return build(raw, live)
}
