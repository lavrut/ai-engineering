// contract — the Go twin of data/python/contract.py: types, JSON and contract loading.
//
// Go reads the JSON export of a contract (contract.py --export writes <stem>.contract.json);
// the YAML stays the source. Values keep Python's view of JSON: object keys in order,
// integers apart from floats, and NaN / Infinity accepted, so details print byte for byte
// what the Python validator prints.
package main

import (
	"errors"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf16"
	"unicode/utf8"
)

// ── 1. JSON values ──────────────────────────────────────────────────────────
// encoding/json loses key order and the text of a number, and both show up in details
// (json.dumps of a value, 1 against 1.0), so values are parsed here as Python's json does.

// Object is a JSON object with its keys in first-seen order; a repeated key keeps its
// first position and its last value, as a Python dict does.
type Object struct {
	Keys []string
	Vals map[string]any
}

// Get returns the value under k and whether the key is present.
func (o *Object) Get(k string) (any, bool) {
	v, ok := o.Vals[k]
	return v, ok
}

func (o *Object) set(k string, v any) {
	if _, ok := o.Vals[k]; !ok {
		o.Keys = append(o.Keys, k)
	}
	o.Vals[k] = v
}

// Number is a JSON number as written (or NaN, Infinity, -Infinity, which Python accepts).
type Number string

// IsInt says whether Python's json would read the number as an int rather than a float.
func (n Number) IsInt() bool {
	return !strings.ContainsAny(string(n), ".eEIN")
}

// Float is the number as a float64; out of range is ±Inf, as in Python.
func (n Number) Float() float64 {
	switch n {
	case "NaN":
		return math.NaN()
	case "Infinity":
		return math.Inf(1)
	case "-Infinity":
		return math.Inf(-1)
	}
	f, _ := strconv.ParseFloat(string(n), 64)
	return f
}

func (n Number) bigInt() *big.Int {
	b, _ := new(big.Int).SetString(string(n), 10)
	return b
}

type jsonParser struct {
	s string
	i int
}

var errJSON = errors.New("invalid JSON")

// ParseJSON reads one JSON document the way Python's json.loads reads a str.
func ParseJSON(s string) (any, error) {
	p := &jsonParser{s: s}
	p.ws()
	v, err := p.value()
	if err != nil {
		return nil, err
	}
	p.ws()
	if p.i != len(p.s) {
		return nil, errJSON
	}
	return v, nil
}

func (p *jsonParser) ws() {
	for p.i < len(p.s) && strings.IndexByte(" \t\n\r", p.s[p.i]) >= 0 {
		p.i++
	}
}

func (p *jsonParser) lit(word string) bool {
	if strings.HasPrefix(p.s[p.i:], word) {
		p.i += len(word)
		return true
	}
	return false
}

func (p *jsonParser) value() (any, error) {
	if p.i >= len(p.s) {
		return nil, errJSON
	}
	switch c := p.s[p.i]; {
	case c == '{':
		return p.object()
	case c == '[':
		return p.array()
	case c == '"':
		return p.str()
	case p.lit("null"):
		return nil, nil
	case p.lit("true"):
		return true, nil
	case p.lit("false"):
		return false, nil
	case p.lit("NaN"):
		return Number("NaN"), nil
	case p.lit("Infinity"):
		return Number("Infinity"), nil
	case p.lit("-Infinity"):
		return Number("-Infinity"), nil
	case c == '-' || (c >= '0' && c <= '9'):
		return p.number()
	}
	return nil, errJSON
}

func (p *jsonParser) digits() int {
	start := p.i
	for p.i < len(p.s) && p.s[p.i] >= '0' && p.s[p.i] <= '9' {
		p.i++
	}
	return p.i - start
}

// number matches -?(0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?, as Python's scanner does; a
// fraction or exponent without digits is left unread, so it fails as extra data.
func (p *jsonParser) number() (any, error) {
	start := p.i
	if p.s[p.i] == '-' {
		p.i++
	}
	if p.i < len(p.s) && p.s[p.i] == '0' {
		p.i++
	} else if p.digits() == 0 {
		return nil, errJSON
	}
	if mark := p.i; p.i < len(p.s) && p.s[p.i] == '.' {
		p.i++
		if p.digits() == 0 {
			p.i = mark
		}
	}
	if mark := p.i; p.i < len(p.s) && (p.s[p.i] == 'e' || p.s[p.i] == 'E') {
		p.i++
		if p.i < len(p.s) && (p.s[p.i] == '+' || p.s[p.i] == '-') {
			p.i++
		}
		if p.digits() == 0 {
			p.i = mark
		}
	}
	n := Number(p.s[start:p.i])
	if n.IsInt() && len(strings.TrimPrefix(string(n), "-")) > 4300 {
		return nil, errJSON // Python's int() refuses more than 4300 digits
	}
	return n, nil
}

func (p *jsonParser) hex4() (rune, bool) {
	if p.i+4 > len(p.s) {
		return 0, false
	}
	n, err := strconv.ParseUint(p.s[p.i:p.i+4], 16, 32)
	if err != nil || strings.ContainsAny(p.s[p.i:p.i+4], "+-") {
		return 0, false
	}
	p.i += 4
	return rune(n), true
}

// str reads a string strictly: control characters must be escaped.
func (p *jsonParser) str() (any, error) {
	p.i++
	var b strings.Builder
	for {
		if p.i >= len(p.s) {
			return nil, errJSON
		}
		c := p.s[p.i]
		switch {
		case c == '"':
			p.i++
			return b.String(), nil
		case c < 0x20:
			return nil, errJSON
		case c != '\\':
			b.WriteByte(c)
			p.i++
			continue
		}
		p.i++
		if p.i >= len(p.s) {
			return nil, errJSON
		}
		e := p.s[p.i]
		p.i++
		if j := strings.IndexByte(`"\/bfnrt`, e); j >= 0 {
			b.WriteByte("\"\\/\b\f\n\r\t"[j])
			continue
		}
		if e != 'u' {
			return nil, errJSON
		}
		r, ok := p.hex4()
		if !ok {
			return nil, errJSON
		}
		if utf16.IsSurrogate(r) && r < 0xdc00 && strings.HasPrefix(p.s[p.i:], `\u`) {
			mark := p.i
			p.i += 2
			if lo, ok := p.hex4(); ok && lo >= 0xdc00 && lo <= 0xdfff {
				r = utf16.DecodeRune(r, lo)
			} else {
				p.i = mark
			}
		}
		b.WriteRune(r) // a lone surrogate becomes U+FFFD: Go strings are UTF-8
	}
}

func (p *jsonParser) array() (any, error) {
	p.i++
	out := []any{}
	p.ws()
	if p.lit("]") {
		return out, nil
	}
	for {
		p.ws()
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		out = append(out, v)
		p.ws()
		if p.lit("]") {
			return out, nil
		}
		if !p.lit(",") {
			return nil, errJSON
		}
	}
}

func (p *jsonParser) object() (any, error) {
	p.i++
	o := &Object{Vals: map[string]any{}}
	p.ws()
	if p.lit("}") {
		return o, nil
	}
	for {
		p.ws()
		if p.i >= len(p.s) || p.s[p.i] != '"' {
			return nil, errJSON
		}
		k, err := p.str()
		if err != nil {
			return nil, err
		}
		p.ws()
		if !p.lit(":") {
			return nil, errJSON
		}
		p.ws()
		v, err := p.value()
		if err != nil {
			return nil, err
		}
		o.set(k.(string), v)
		p.ws()
		if p.lit("}") {
			return o, nil
		}
		if !p.lit(",") {
			return nil, errJSON
		}
	}
}

// ── 2. Showing values ───────────────────────────────────────────────────────

// Show renders a value as json.dumps(x, ensure_ascii=False) does.
func Show(v any) string {
	switch x := v.(type) {
	case nil:
		return "null"
	case bool:
		if x {
			return "true"
		}
		return "false"
	case string:
		return quote(x)
	case Number:
		return showNumber(x)
	case []any:
		parts := make([]string, len(x))
		for i, e := range x {
			parts[i] = Show(e)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case *Object:
		parts := make([]string, len(x.Keys))
		for i, k := range x.Keys {
			parts[i] = quote(k) + ": " + Show(x.Vals[k])
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return fmt.Sprint(v)
}

func quote(s string) string {
	var b strings.Builder
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
				fmt.Fprintf(&b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}

func showNumber(n Number) string {
	if n.IsInt() {
		return n.bigInt().String()
	}
	return floatRepr(n.Float())
}

// floatRepr is Python's repr of a float as json.dumps writes it: shortest digits, fixed
// notation for 1e-4 <= |x| < 1e16 with at least one decimal, else 1.5e-07 style.
func floatRepr(f float64) string {
	switch {
	case math.IsNaN(f):
		return "NaN"
	case math.IsInf(f, 1):
		return "Infinity"
	case math.IsInf(f, -1):
		return "-Infinity"
	}
	sign := ""
	if math.Signbit(f) {
		sign, f = "-", -f
	}
	if f == 0 {
		return sign + "0.0"
	}
	e := strconv.FormatFloat(f, 'e', -1, 64)
	mant, exp, _ := strings.Cut(e, "e")
	digits := strings.Replace(mant, ".", "", 1)
	x, _ := strconv.Atoi(exp)
	decpt := x + 1
	if decpt <= -4 || decpt > 16 {
		m := digits[:1]
		if len(digits) > 1 {
			m += "." + digits[1:]
		}
		es := "+"
		if x < 0 {
			es, x = "-", -x
		}
		return fmt.Sprintf("%s%se%s%02d", sign, m, es, x)
	}
	switch {
	case decpt <= 0:
		return sign + "0." + strings.Repeat("0", -decpt) + digits
	case decpt >= len(digits):
		return sign + digits + strings.Repeat("0", decpt-len(digits)) + ".0"
	}
	return sign + digits[:decpt] + "." + digits[decpt:]
}

// num is Python's f"{x:g}": 6 significant digits, trailing zeros dropped.
func num(x float64) string {
	switch {
	case math.IsNaN(x):
		return "nan"
	case math.IsInf(x, 1):
		return "inf"
	case math.IsInf(x, -1):
		return "-inf"
	}
	return strconv.FormatFloat(x, 'g', 6, 64)
}

// pyRepr is Python's repr of a str, used where contract.py writes {name!r}.
func pyRepr(s string) string {
	q := byte('\'')
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		q = '"'
	}
	var b strings.Builder
	b.WriteByte(q)
	for _, r := range s {
		switch {
		case r == rune(q) || r == '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case unicode.IsPrint(r):
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

// jsonType names a value's JSON type as contract.py's _json_type does.
func jsonType(v any) string {
	switch x := v.(type) {
	case nil:
		return "null"
	case bool:
		return "boolean"
	case Number:
		if x.IsInt() {
			return "integer"
		}
		return "number"
	case string:
		return "string"
	case []any:
		return "array"
	}
	return "object"
}

func typeOK(v any, kind string) bool {
	got := jsonType(v)
	return got == kind || (kind == "number" && got == "integer")
}

// ── 3. Contracts ────────────────────────────────────────────────────────────
var (
	sections     = []string{"info", "schema", "quality", "servicing", "consumers"}
	fieldTypes   = []string{"string", "integer", "number", "boolean", "object", "array"}
	qualityRules = []string{"row_count", "unique", "enum_coverage"}
)

// ContractError is a malformed contract; the message names the section or field,
// word for word as contract.py's ContractError.
type ContractError string

func (e ContractError) Error() string { return string(e) }

func cerr(format string, a ...any) error { return ContractError(fmt.Sprintf(format, a...)) }

// Field is one schema field; Pattern is matched in full, as re.fullmatch does.
type Field struct {
	Name, Type string
	Required   bool
	Enum       []any
	HasEnum    bool
	Pattern    string
	HasPattern bool
	rx         *regexp.Regexp
}

// Contract is the part of a contract the rules read.
type Contract struct {
	Fields         []Field
	HasRowCount    bool
	RowMin, RowMax *big.Int
	Unique         []string
	EnumCoverage   []string
	MaxAgeDays     float64
}

func isInt(v any) bool {
	n, ok := v.(Number)
	return ok && n.IsInt()
}

func has(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

func mapping(v any, where string) (*Object, error) {
	o, ok := v.(*Object)
	if !ok {
		return nil, cerr("%s must be a mapping", where)
	}
	return o, nil
}

func text(m *Object, key, where string) (string, error) {
	s, ok := m.Vals[key].(string)
	if !ok || s == "" {
		return "", cerr("%s.%s must be a non-empty string", where, key)
	}
	return s, nil
}

func version(v any, where string) error {
	if s, ok := v.(string); isInt(v) || (ok && s != "") {
		return nil
	}
	return cerr("%s must be a non-empty string or integer", where)
}

// isDate is re.fullmatch(r"\d{4}-\d{2}-\d{2}"), where \d is any Unicode decimal digit.
func isDate(v any) bool {
	s, ok := v.(string)
	if !ok {
		return false
	}
	rs := []rune(s)
	if len(rs) != 10 {
		return false
	}
	for i, r := range rs {
		if (i == 4 || i == 7) != (r == '-') || (i != 4 && i != 7 && !unicode.IsDigit(r)) {
			return false
		}
	}
	return true
}

func checkInfo(v any) error {
	info, err := mapping(v, "info")
	if err != nil {
		return err
	}
	if _, err := text(info, "id", "info"); err != nil {
		return err
	}
	if err := version(info.Vals["version"], "info.version"); err != nil {
		return err
	}
	status, _ := info.Vals["status"].(string)
	if status != "active" && status != "deprecated" {
		return cerr("info.status must be one of active, deprecated")
	}
	for _, k := range []string{"owner", "description"} {
		if _, err := text(info, k, "info"); err != nil {
			return err
		}
	}
	if s, ok := info.Get("sunset"); ok {
		if !isDate(s) {
			return cerr("info.sunset must be a date like 2026-12-01")
		}
	} else if status == "deprecated" {
		return cerr("info.sunset is required when info.status is deprecated")
	}
	if _, ok := info.Get("generated_at"); ok {
		if _, err := text(info, "generated_at", "info"); err != nil {
			return err
		}
	}
	return nil
}

// ── 4. Patterns (ruling D7) ─────────────────────────────────────────────────
// A pattern is checked against an allow-list grammar by the same small tokenizer as
// contract.py's _tokenize, so a pattern either means the same thing in both engines or is
// refused by both. \d \w \s become explicit ASCII classes, ^ and $ are dropped (the match
// is already full), and the rest passes through unchanged.
const (
	patternMeta  = `.*+?()[]{}|^$\-`
	classEscapes = "dDwWsS"
	maxRepeat    = 1000
)

var (
	classOut = map[rune]string{'d': "[0-9]", 'D': "[^0-9]", 'w': "[0-9A-Za-z_]", 'W': "[^0-9A-Za-z_]",
		's': `[ \t\n\r\f]`, 'S': `[^ \t\n\r\f]`}
	classIn = map[rune]string{'d': "0-9", 'w': "0-9A-Za-z_", 's': ` \t\n\r\f`,
		'D': `\x00-\x2f\x3a-\x{10FFFF}`,
		'W': `\x00-\x2f\x3a-\x40\x5b-\x5e\x60\x7b-\x{10FFFF}`,
		'S': `\x00-\x08\x0b\x0e-\x1f\x21-\x{10FFFF}`}
	repeatRe = regexp.MustCompile(`^\{([0-9]+)(,([0-9]*))?\}`)
)

// badPattern is the fixed refusal: the same text as contract.py, never an engine's error.
type badPattern string

func (b badPattern) Error() string { return "invalid pattern: " + string(b) }

type patToken struct{ src, eng string }

func classLit(c rune) string {
	if strings.ContainsRune(`\]^-[`, c) {
		return `\` + string(c)
	}
	return string(c)
}

// bracket reads one bracket class from p[i] == '[': its token and the index after it.
func bracket(p []rune, i int) (patToken, int, error) {
	j, neg := i+1, ""
	if j < len(p) && p[j] == '^' {
		j, neg = j+1, "^"
	}
	type item struct {
		kind byte // l: literal, e: class escape, d: unescaped dash
		c    rune
	}
	var items []item
	first := j
	for {
		if j >= len(p) {
			return patToken{}, 0, badPattern("[")
		}
		c := p[j]
		if c == ']' {
			if j == first {
				return patToken{}, 0, badPattern(string(p[i : j+1]))
			}
			break
		}
		if c == '[' {
			return patToken{}, 0, badPattern("[")
		}
		if c == '\\' {
			if j+1 >= len(p) {
				return patToken{}, 0, badPattern(`\`)
			}
			switch e := p[j+1]; {
			case strings.ContainsRune(classEscapes, e):
				items = append(items, item{'e', e})
			case strings.ContainsRune(patternMeta, e):
				items = append(items, item{'l', e})
			default:
				return patToken{}, 0, badPattern(`\` + string(e))
			}
			j += 2
			continue
		}
		kind := byte('l')
		if c == '-' {
			kind = 'd'
		}
		items = append(items, item{kind, c})
		j++
	}
	var out strings.Builder
	for k := 0; k < len(items); {
		it := items[k]
		if it.kind != 'e' && k+2 < len(items) && items[k+1].kind == 'd' {
			hi := items[k+2]
			if hi.kind == 'e' || it.c > hi.c {
				return patToken{}, 0, badPattern(string(it.c) + "-" + string(hi.c))
			}
			out.WriteString(classLit(it.c) + "-" + classLit(hi.c))
			k += 3
			continue
		}
		if it.kind == 'd' && k > 0 && k < len(items)-1 {
			return patToken{}, 0, badPattern("-")
		}
		if it.kind == 'e' {
			out.WriteString(classIn[it.c])
		} else {
			out.WriteString(classLit(it.c))
		}
		k++
	}
	return patToken{string(p[i : j+1]), "[" + neg + out.String() + "]"}, j + 1, nil
}

// repeatCount reads a {n,m} bound, capped just above the limit so huge numbers cannot overflow.
func repeatCount(s string) int {
	if len(s) > 4 {
		return maxRepeat + 1
	}
	n, _ := strconv.Atoi(s)
	return n
}

// tokenize is contract.py's _tokenize, by code point: (source, engine form) per token.
func tokenize(pat string) ([]patToken, error) {
	p := []rune(pat)
	var toks []patToken
	depth, prev := 0, ""
	for i := 0; i < len(p); {
		c, kind, next := p[i], "atom", -1
		var tok patToken
		switch {
		case c == '\\':
			if i+1 >= len(p) {
				return nil, badPattern(`\`)
			}
			e := p[i+1]
			if !strings.ContainsRune(patternMeta, e) && !strings.ContainsRune(classEscapes, e) {
				return nil, badPattern(`\` + string(e))
			}
			src := string(p[i : i+2])
			tok = patToken{src, src}
			if s, ok := classOut[e]; ok {
				tok.eng = s
			}
		case c == '[':
			t, n, err := bracket(p, i)
			if err != nil {
				return nil, err
			}
			tok, next = t, n
		case c == '(':
			rest := string(p[i:])
			switch {
			case strings.HasPrefix(rest, "(?:"):
				tok = patToken{"(?:", "(?:"}
			case strings.HasPrefix(rest, "(?"):
				return nil, badPattern(string(p[i:min(i+3, len(p))]))
			default:
				tok = patToken{"(", "("}
			}
			depth, kind = depth+1, "open"
		case c == ')':
			if depth == 0 {
				return nil, badPattern(")")
			}
			depth, tok = depth-1, patToken{")", ")"}
		case c == '|':
			tok, kind = patToken{"|", "|"}, "alt"
		case strings.ContainsRune("*+?{", c):
			src := string(c)
			if c == '{' {
				m := repeatRe.FindStringSubmatch(string(p[i:]))
				if m == nil {
					for k := i; k < len(p); k++ {
						if p[k] == '}' {
							return nil, badPattern(string(p[i : k+1]))
						}
					}
					return nil, badPattern("{")
				}
				src = m[0]
				lo := repeatCount(m[1])
				if lo > maxRepeat || (m[3] != "" && (repeatCount(m[3]) < lo || repeatCount(m[3]) > maxRepeat)) {
					return nil, badPattern(src)
				}
			}
			if prev != "atom" {
				return nil, badPattern(src)
			}
			if after := i + len([]rune(src)); after < len(p) && (p[after] == '?' || p[after] == '+') {
				return nil, badPattern(src + string(p[after]))
			}
			tok, kind = patToken{src, src}, "quant"
		case c == '^':
			if len(toks) > 0 {
				return nil, badPattern("^")
			}
			tok, kind = patToken{"^", ""}, "anchor"
		case c == '$':
			if i != len(p)-1 {
				return nil, badPattern("$")
			}
			tok, kind = patToken{"$", ""}, "anchor"
		case c == ']' || c == '}':
			return nil, badPattern(string(c))
		default:
			tok = patToken{string(c), string(c)}
		}
		if next >= 0 {
			i = next
		} else {
			i += len([]rune(tok.src))
		}
		toks = append(toks, tok)
		prev = kind
	}
	if depth > 0 {
		return nil, badPattern("(")
	}
	return toks, nil
}

// patternTokens is the pattern's tokens under ruling D7, or the fixed refusal.
func patternTokens(p string) ([]string, error) {
	toks, err := tokenize(p)
	if err != nil {
		return nil, err
	}
	out := make([]string, len(toks))
	for i, t := range toks {
		out[i] = t.src
	}
	return out, nil
}

// engineForm is the pattern as handed to RE2, before the full-match wrapper.
func engineForm(p string) (string, error) {
	toks, err := tokenize(p)
	if err != nil {
		return "", err
	}
	var b strings.Builder
	for _, t := range toks {
		b.WriteString(t.eng)
	}
	return b.String(), nil
}

// compilePattern compiles a D7 pattern as a full match.
func compilePattern(p string) (*regexp.Regexp, error) {
	eng, err := engineForm(p)
	if err != nil {
		return nil, err
	}
	rx, err := regexp.Compile(`^(?:` + eng + `)$`)
	if err != nil {
		return nil, badPattern(p) // e.g. nested repeats over RE2's size limit
	}
	return rx, nil
}

// ── 5. Schema, quality, servicing, consumers ─────────────────────────────────

func checkSchema(v any) ([]Field, error) {
	schema, err := mapping(v, "schema")
	if err != nil {
		return nil, err
	}
	list, ok := schema.Vals["fields"].([]any)
	if !ok || len(list) == 0 {
		return nil, cerr("schema.fields must be a non-empty list")
	}
	seen := map[string]bool{}
	var out []Field
	for i, item := range list {
		where := fmt.Sprintf("schema.fields[%d]", i)
		fld, err := mapping(item, where)
		if err != nil {
			return nil, err
		}
		name, err := text(fld, "name", where)
		if err != nil {
			return nil, err
		}
		if seen[name] {
			return nil, cerr("%s.name %s is declared twice", where, pyRepr(name))
		}
		seen[name] = true
		kind, _ := fld.Vals["type"].(string)
		if !has(fieldTypes, kind) {
			return nil, cerr("%s.type must be one of %s", where, strings.Join(fieldTypes, ", "))
		}
		req, ok := fld.Vals["required"].(bool)
		if !ok {
			return nil, cerr("%s.required must be true or false", where)
		}
		if _, err := text(fld, "meaning", where); err != nil {
			return nil, err
		}
		f := Field{Name: name, Type: kind, Required: req}
		if e, ok := fld.Get("enum"); ok {
			enum, ok := e.([]any)
			if !ok || len(enum) == 0 {
				return nil, cerr("%s.enum must be a non-empty list", where)
			}
			for _, x := range enum {
				if !typeOK(x, kind) {
					return nil, cerr("%s.enum value %s is not of type %s", where, Show(x), kind)
				}
			}
			f.Enum, f.HasEnum = enum, true
		}
		if pv, ok := fld.Get("pattern"); ok {
			if kind != "string" {
				return nil, cerr("%s.pattern needs type string", where)
			}
			pat, ok := pv.(string)
			if !ok {
				return nil, cerr("%s.pattern must be a string", where)
			}
			rx, err := compilePattern(pat)
			if err != nil {
				return nil, cerr("%s.pattern: %v", where, err)
			}
			f.Pattern, f.HasPattern = pat, true
			f.rx = rx
		}
		out = append(out, f)
	}
	return out, nil
}

func checkQuality(v any, fields map[string]*Field, c *Contract) error {
	quality, err := mapping(v, "quality")
	if err != nil {
		return err
	}
	for _, k := range quality.Keys {
		if !has(qualityRules, k) {
			return cerr("quality.%s is not a known rule (%s)", k, strings.Join(qualityRules, ", "))
		}
	}
	if rv, ok := quality.Get("row_count"); ok {
		rc, err := mapping(rv, "quality.row_count")
		if err != nil {
			return err
		}
		for _, k := range rc.Keys {
			if k != "min" && k != "max" {
				return cerr("quality.row_count.%s is not min or max", k)
			}
			if !isInt(rc.Vals[k]) || rc.Vals[k].(Number).bigInt().Sign() < 0 {
				return cerr("quality.row_count.%s must be a non-negative integer", k)
			}
		}
		if len(rc.Keys) == 0 {
			return cerr("quality.row_count needs min or max")
		}
		c.HasRowCount = true
		if x, ok := rc.Get("min"); ok {
			c.RowMin = x.(Number).bigInt()
		}
		if x, ok := rc.Get("max"); ok {
			c.RowMax = x.(Number).bigInt()
		}
		if c.RowMin != nil && c.RowMax != nil && c.RowMin.Cmp(c.RowMax) > 0 {
			return cerr("quality.row_count.min is above max")
		}
	}
	for _, key := range []string{"unique", "enum_coverage"} {
		lv, ok := quality.Get(key)
		if !ok {
			continue
		}
		list, ok := lv.([]any)
		names := make([]string, len(list))
		for i, x := range list {
			s, isStr := x.(string)
			ok = ok && isStr
			names[i] = s
		}
		if !ok {
			return cerr("quality.%s must be a list of field names", key)
		}
		for _, n := range names {
			f := fields[n]
			if f == nil {
				return cerr("quality.%s names %s, which is not a schema field", key, pyRepr(n))
			}
			if key == "enum_coverage" && !f.HasEnum {
				return cerr("quality.enum_coverage names %s, which has no enum", pyRepr(n))
			}
		}
		if key == "unique" {
			c.Unique = names
		} else {
			c.EnumCoverage = names
		}
	}
	return nil
}

func checkServicing(v any, c *Contract) error {
	servicing, err := mapping(v, "servicing")
	if err != nil {
		return err
	}
	fr, err := mapping(servicing.Vals["freshness"], "servicing.freshness")
	if err != nil {
		return err
	}
	age, ok := fr.Vals["max_age_days"].(Number)
	if !ok || age.Float() < 0 {
		return cerr("servicing.freshness.max_age_days must be a non-negative number")
	}
	c.MaxAgeDays = age.Float()
	return nil
}

func checkConsumers(v any) error {
	list, ok := v.([]any)
	if !ok {
		return cerr("consumers must be a list")
	}
	for i, item := range list {
		where := fmt.Sprintf("consumers[%d]", i)
		con, err := mapping(item, where)
		if err != nil {
			return err
		}
		if _, err := text(con, "name", where); err != nil {
			return err
		}
		if err := version(con.Vals["version"], where+".version"); err != nil {
			return err
		}
		if _, err := text(con, "reads", where); err != nil {
			return err
		}
	}
	return nil
}

// LoadContract checks a parsed contract in contract.py's order, so the first problem
// found is the one Python names.
func LoadContract(v any) (*Contract, error) {
	doc, err := mapping(v, "contract")
	if err != nil {
		return nil, err
	}
	for _, s := range sections {
		if _, ok := doc.Get(s); !ok {
			return nil, cerr("missing section: %s", s)
		}
	}
	for _, k := range doc.Keys {
		if !has(sections, k) {
			return nil, cerr("unknown section: %s", k)
		}
	}
	if err := checkInfo(doc.Vals["info"]); err != nil {
		return nil, err
	}
	c := &Contract{}
	if c.Fields, err = checkSchema(doc.Vals["schema"]); err != nil {
		return nil, err
	}
	byName := map[string]*Field{}
	for i := range c.Fields {
		byName[c.Fields[i].Name] = &c.Fields[i]
	}
	if err := checkQuality(doc.Vals["quality"], byName, c); err != nil {
		return nil, err
	}
	if err := checkServicing(doc.Vals["servicing"], c); err != nil {
		return nil, err
	}
	if err := checkConsumers(doc.Vals["consumers"]); err != nil {
		return nil, err
	}
	return c, nil
}

// LoadContractFile reads a <stem>.contract.json export.
func LoadContractFile(path string) (*Contract, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, cerr("contract file not found: %s", filepath.Base(path))
	}
	v, err := ParseJSON(string(raw))
	if err != nil || !utf8.Valid(raw) {
		return nil, cerr("%s is not a valid contract file", filepath.Base(path))
	}
	return LoadContract(v)
}
