// config: the gateway config, read from the JSON export (infra/data/fixtures/config.json, written
// by infra/python/config.py --export). Go never parses YAML. The rules are config.py's, with the
// same error wording for the key; the upstream must be loopback (the twin has no live mode).
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/big"
	"os"
	"sort"
	"strconv"
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
	if !ok {
		return false
	}
	for _, l := range loopback {
		if h == l {
			return true
		}
	}
	return false
}

func mapping(x any, where string, keys []string, optional ...string) (*Object, error) {
	o, ok := x.(*Object)
	if !ok {
		return nil, cerr("%s must be a mapping", where)
	}
	for _, k := range o.Keys {
		if !has(keys, k) && !has(optional, k) {
			return nil, cerr("unknown key %s in %s", pyRepr(k), where)
		}
	}
	for _, k := range keys {
		if _, ok := o.Get(k); !ok {
			return nil, cerr("%s.%s is missing", where, k)
		}
	}
	return o, nil
}

func positiveNum(x any, where string) (float64, error) {
	n, ok := x.(Number)
	f := 0.0
	if ok {
		f = n.Float()
	}
	if !ok || math.IsInf(f, 0) || math.IsNaN(f) || f <= 0 {
		return 0, cerr("%s must be a finite number above zero, got %s", where, pyRepr(x))
	}
	return f, nil
}

func positiveInt(x any, where string) (int, error) {
	n, ok := x.(Number)
	if !ok || !n.IsInt() || n.bigInt().Sign() <= 0 {
		return 0, cerr("%s must be an integer above zero, got %s", where, pyRepr(x))
	}
	if !n.bigInt().IsInt64() || n.bigInt().Int64() > math.MaxInt32 {
		return math.MaxInt32, nil // Python's ints have no ceiling; ours stops well past any rate
	}
	return int(n.bigInt().Int64()), nil
}

func name(x any, where string) (string, error) {
	s, ok := x.(string)
	if !ok || s == "" {
		return "", cerr("%s must be a non-empty string", where)
	}
	return s, nil
}

func price(x any, where string, m *ModelCfg) error {
	if x == "lab" {
		m.Lab = true
		return nil
	}
	if l, ok := x.([]any); ok && len(l) == 2 {
		for i := range 2 {
			f, err := positiveNum(l[i], fmt.Sprintf("%s[%d]", where, i))
			if err != nil {
				return err
			}
			m.Price[i] = f
		}
		return nil
	}
	return cerr("%s must be 'lab' or [input, output] per million tokens", where)
}

func has(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
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

// FromExport is config.from_dict: the export's shape turned back into the YAML's, then build.
func FromExport(d any, live bool) (Config, error) {
	o, ok := d.(*Object)
	if !ok {
		return Config{}, cerr("config must be a mapping")
	}
	top := []string{"upstream", "models", "routes", "deny_unknown", "cache", "retry", "limit", "budgets", "untagged", "traces_dir"}
	for _, k := range o.Keys {
		if !has(top, k) {
			return Config{}, cerr("unknown key %s at top level", pyRepr(k))
		}
	}
	raw := &Object{}
	for _, k := range []string{"upstream", "models", "cache", "retry", "limit"} {
		if v, ok := o.Get(k); ok {
			raw.Set(k, v)
		}
	}
	if r, ok := o.Get("routes"); ok {
		if ro, ok := r.(*Object); ok {
			routes := ro.Copy()
			if du, _ := o.Get("deny_unknown"); truthy(du) {
				routes.Set("*", "deny")
			}
			raw.Set("routes", routes)
		}
	}
	if b, ok := o.Get("budgets"); ok {
		if bo, ok := b.(*Object); ok {
			budgets := &Object{}
			for _, t := range bo.Keys {
				v, _ := bo.Get(t)
				if team, ok := v.(*Object); ok {
					kept := &Object{}
					for _, k := range team.Keys {
						x, _ := team.Get(k)
						if !(k == "warn_usd" && x == nil) {
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
	}
	if t, ok := o.Get("traces_dir"); ok {
		raw.Set("traces", &Object{Keys: []string{"dir"}, vals: map[string]any{"dir": t}})
	}
	return build(raw, live)
}

// build is every rule over the YAML's shape, in config.py's order.
func build(raw *Object, live bool) (Config, error) {
	var c Config
	for _, k := range raw.Keys {
		if !has(sections, k) {
			return c, cerr("unknown key %s at top level", pyRepr(k))
		}
	}
	for _, k := range sections {
		if _, ok := raw.Get(k); !ok {
			return c, cerr("missing section: %s", k)
		}
	}
	get := func(k string) any { v, _ := raw.Get(k); return v }

	up, err := name(get("upstream"), "upstream")
	if err != nil {
		return c, err
	}
	if !live && !isLoopback(up) {
		h, ok := hostname(up)
		if !ok {
			return c, fmt.Errorf("upstream %s: Invalid IPv6 URL", up) // urlsplit raises (not a ConfigError)
		}
		if h == "" {
			h = "None"
		}
		return c, cerr("upstream %s is not loopback; a remote host such as %s needs live mode", up, h)
	}
	c.Upstream = up

	mo, ok := get("models").(*Object)
	if !ok || len(mo.Keys) == 0 {
		return c, cerr("models must be a non-empty mapping")
	}
	c.Models = map[string]ModelCfg{}
	for _, n := range mo.Keys {
		w := "models." + n
		v, _ := mo.Get(n)
		m, err := mapping(v, w, []string{"rpm", "tpm", "price"})
		if err != nil {
			return c, err
		}
		var mc ModelCfg
		if mc.RPM, err = positiveInt(m.MustGet("rpm"), w+".rpm"); err != nil {
			return c, err
		}
		if mc.TPM, err = positiveInt(m.MustGet("tpm"), w+".tpm"); err != nil {
			return c, err
		}
		if err := price(m.MustGet("price"), w+".price", &mc); err != nil {
			return c, err
		}
		c.Models[n] = mc
	}

	ro, ok := get("routes").(*Object)
	if !ok {
		return c, cerr("routes must be a mapping")
	}
	c.Routes = map[string]RouteCfg{}
	for _, n := range ro.Keys {
		v, _ := ro.Get(n)
		if n == "*" {
			if v != "deny" {
				return c, cerr("routes.'*' must be deny")
			}
			c.DenyUnknown = true
			continue
		}
		w := "routes." + n
		r, err := mapping(v, w, []string{"to"}, "fallback")
		if err != nil {
			return c, err
		}
		fb, ok := r.Get("fallback")
		if !ok {
			fb = []any{}
		}
		fl, ok := fb.([]any)
		if !ok {
			return c, cerr("%s.fallback must be a list", w)
		}
		rc := RouteCfg{}
		for i, t := range append([]any{r.MustGet("to")}, fl...) {
			s, isStr := t.(string)
			if _, known := c.Models[s]; !isStr || !known {
				return c, cerr("%s names %s, which is not under models", w, pyStr(t))
			}
			if i == 0 {
				rc.To = s
			} else {
				rc.Fallback = append(rc.Fallback, s)
			}
		}
		if rc.Fallback == nil {
			rc.Fallback = []string{}
		}
		c.Routes[n] = rc
	}

	cm, err := mapping(get("cache"), "cache", []string{"ttl_s", "skip_tools"})
	if err != nil {
		return c, err
	}
	st, ok := cm.MustGet("skip_tools").(bool)
	if !ok {
		return c, cerr("cache.skip_tools must be true or false")
	}
	if c.Cache.TTLs, err = positiveInt(cm.MustGet("ttl_s"), "cache.ttl_s"); err != nil {
		return c, err
	}
	c.Cache.SkipTools = st

	rm, err := mapping(get("retry"), "retry", []string{"attempts", "base_ms", "max_ms"})
	if err != nil {
		return c, err
	}
	for _, f := range []struct {
		k string
		p *int
	}{{"attempts", &c.Retry.Attempts}, {"base_ms", &c.Retry.BaseMs}, {"max_ms", &c.Retry.MaxMs}} {
		if *f.p, err = positiveInt(rm.MustGet(f.k), "retry."+f.k); err != nil {
			return c, err
		}
	}
	if c.Retry.MaxMs < c.Retry.BaseMs {
		return c, cerr("retry.max_ms must not be below retry.base_ms")
	}

	lm, err := mapping(get("limit"), "limit", []string{"max_wait_ms"})
	if err != nil {
		return c, err
	}
	if c.Limit.MaxWaitMs, err = positiveInt(lm.MustGet("max_wait_ms"), "limit.max_wait_ms"); err != nil {
		return c, err
	}

	bo, ok := get("budgets").(*Object)
	if !ok {
		return c, cerr("budgets must be a mapping")
	}
	c.Budgets = map[string]BudgetCfg{}
	untagged, haveUntagged := "", false
	for _, team := range bo.Keys {
		v, _ := bo.Get(team)
		if team == "untagged" {
			if untagged, err = name(v, "budgets.untagged"); err != nil {
				return c, err
			}
			haveUntagged = true
			continue
		}
		w := "budgets." + team
		b, err := mapping(v, w, []string{"limit_usd"}, "warn_usd")
		if err != nil {
			return c, err
		}
		lim, err := positiveNum(b.MustGet("limit_usd"), w+".limit_usd")
		if err != nil {
			return c, err
		}
		bc := BudgetCfg{LimitUSD: lim}
		if x, ok := b.Get("warn_usd"); ok {
			warn, err := positiveNum(x, w+".warn_usd")
			if err != nil {
				return c, err
			}
			if warn > lim {
				return c, cerr("%s.warn_usd (%s) is above limit_usd (%s)", w, floatRepr(warn), floatRepr(lim))
			}
			bc.WarnUSD = &warn
		}
		c.Budgets[team] = bc
	}
	if !haveUntagged {
		return c, cerr("budgets.untagged is missing (refuse, or a team name)")
	}
	if _, ok := c.Budgets[untagged]; untagged != "refuse" && !ok {
		return c, cerr("budgets.untagged names %s, which is not a team under budgets", untagged)
	}
	c.Untagged = untagged

	tm, err := mapping(get("traces"), "traces", []string{"dir"})
	if err != nil {
		return c, err
	}
	if c.TracesDir, err = name(tm.MustGet("dir"), "traces.dir"); err != nil {
		return c, err
	}
	return c, nil
}

// ── 4. JSON as Python reads and writes it ───────────────────────────────────
// json.loads keeps a document's key order and an integer literal as an int (1.0 is a float);
// json.dumps writes floats by repr. These values carry both, so the twins read alike.

// Object is a JSON object in document order; a repeated key keeps its first place, last value.
type Object struct {
	Keys []string
	vals map[string]any
}

func (o *Object) Get(k string) (any, bool) {
	v, ok := o.vals[k]
	return v, ok
}

func (o *Object) MustGet(k string) any { return o.vals[k] }

func (o *Object) Set(k string, v any) {
	if o.vals == nil {
		o.vals = map[string]any{}
	}
	if _, ok := o.vals[k]; !ok {
		o.Keys = append(o.Keys, k)
	}
	o.vals[k] = v
}

func (o *Object) Copy() *Object {
	c := &Object{}
	for _, k := range o.Keys {
		c.Set(k, o.vals[k])
	}
	return c
}

// Number is a JSON number's literal text.
type Number string

// IsInt: Python reads a literal with no fraction or exponent as an int.
func (n Number) IsInt() bool { return !strings.ContainsAny(string(n), ".eE") }

func (n Number) Float() float64 {
	f, err := strconv.ParseFloat(string(n), 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return math.NaN()
	}
	return f // out of range: ±Inf, as Python's float()
}

func (n Number) bigInt() *big.Int {
	b, _ := new(big.Int).SetString(string(n), 10)
	if b == nil {
		return new(big.Int)
	}
	return b
}

// ParseJSON reads one JSON document into nil, bool, Number, string, []any and *Object.
func ParseJSON(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	v, err := decodeValue(dec)
	if err != nil {
		return nil, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, errors.New("extra data after the document")
	}
	return v, nil
}

func decodeValue(dec *json.Decoder) (any, error) {
	t, err := dec.Token()
	if err != nil {
		return nil, err
	}
	switch x := t.(type) {
	case json.Delim:
		if x == '[' {
			out := []any{}
			for dec.More() {
				v, err := decodeValue(dec)
				if err != nil {
					return nil, err
				}
				out = append(out, v)
			}
			_, err := dec.Token()
			return out, err
		}
		o := &Object{vals: map[string]any{}}
		for dec.More() {
			kt, err := dec.Token()
			if err != nil {
				return nil, err
			}
			v, err := decodeValue(dec)
			if err != nil {
				return nil, err
			}
			o.Set(kt.(string), v)
		}
		_, err := dec.Token()
		return o, err
	case json.Number:
		return Number(x), nil
	default:
		return x, nil // nil, bool, string
	}
}

// truthy is Python's bool(x) over a JSON value.
func truthy(x any) bool {
	switch v := x.(type) {
	case nil:
		return false
	case bool:
		return v
	case Number:
		f := v.Float()
		return f != 0
	case string:
		return v != ""
	case []any:
		return len(v) > 0
	case *Object:
		return len(v.Keys) > 0
	}
	return true
}

// floatRepr is Python's repr of a float: shortest round-trip digits, scientific below 1e-4 or
// from 1e16, and always a ".0" on a whole number.
func floatRepr(f float64) string {
	switch {
	case math.IsNaN(f):
		return "nan"
	case math.IsInf(f, 1):
		return "inf"
	case math.IsInf(f, -1):
		return "-inf"
	}
	if a := math.Abs(f); a != 0 && (a < 1e-4 || a >= 1e16) {
		return strconv.FormatFloat(f, 'e', -1, 64)
	}
	s := strconv.FormatFloat(f, 'f', -1, 64)
	if !strings.Contains(s, ".") {
		s += ".0"
	}
	return s
}

func intText(n Number) string { return n.bigInt().String() }

// pyRepr is Python's repr of a JSON value, as config.py's messages print it.
func pyRepr(x any) string {
	switch v := x.(type) {
	case nil:
		return "None"
	case bool:
		if v {
			return "True"
		}
		return "False"
	case Number:
		if v.IsInt() {
			return intText(v)
		}
		return floatRepr(v.Float())
	case string:
		return strRepr(v)
	case []any:
		parts := make([]string, len(v))
		for i, e := range v {
			parts[i] = pyRepr(e)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case *Object:
		parts := make([]string, len(v.Keys))
		for i, k := range v.Keys {
			parts[i] = strRepr(k) + ": " + pyRepr(v.vals[k])
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return fmt.Sprint(x)
}

// pyStr is Python's str(): a string as itself, anything else as its repr.
func pyStr(x any) string {
	if s, ok := x.(string); ok {
		return s
	}
	return pyRepr(x)
}

func strRepr(s string) string {
	q := byte('\'')
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		q = '"'
	}
	var b strings.Builder
	b.WriteByte(q)
	for _, r := range s {
		switch {
		case r == '\\' || r == rune(q):
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case r == ' ' || unicode.IsPrint(r):
			b.WriteRune(r)
		case r < 0x100:
			fmt.Fprintf(&b, `\x%02x`, r)
		case r < 0x10000:
			fmt.Fprintf(&b, `\u%04x`, r)
		default:
			fmt.Fprintf(&b, `\U%08x`, r)
		}
	}
	b.WriteByte(q)
	return b.String()
}

// Dumps is json.dumps(x, ensure_ascii=False, separators=(",", ":")), with sort_keys when sorted.
func Dumps(x any, sorted bool) string {
	var b strings.Builder
	dump(&b, x, sorted)
	return b.String()
}

func dump(b *strings.Builder, x any, sorted bool) {
	switch v := x.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		b.WriteString(strconv.FormatBool(v))
	case int:
		b.WriteString(strconv.Itoa(v))
	case Number:
		if v.IsInt() {
			b.WriteString(intText(v))
			return
		}
		f := v.Float()
		switch {
		case math.IsNaN(f):
			b.WriteString("NaN")
		case math.IsInf(f, 1):
			b.WriteString("Infinity")
		case math.IsInf(f, -1):
			b.WriteString("-Infinity")
		default:
			b.WriteString(floatRepr(f))
		}
	case string:
		jsonString(b, v)
	case []any:
		b.WriteByte('[')
		for i, e := range v {
			if i > 0 {
				b.WriteByte(',')
			}
			dump(b, e, sorted)
		}
		b.WriteByte(']')
	case *Object:
		keys := append([]string(nil), v.Keys...)
		if sorted {
			sort.Strings(keys) // UTF-8 byte order is code point order, as Python sorts str
		}
		b.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				b.WriteByte(',')
			}
			jsonString(b, k)
			b.WriteByte(':')
			dump(b, v.vals[k], sorted)
		}
		b.WriteByte('}')
	default:
		panic(fmt.Sprintf("dump: %T", x))
	}
}

// jsonString escapes as json.dumps with ensure_ascii=False: quote, backslash and controls only.
func jsonString(b *strings.Builder, s string) {
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			if r < 0x20 {
				fmt.Fprintf(b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}
