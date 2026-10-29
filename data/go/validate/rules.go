package main

import (
	"fmt"
	"os"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"
)

// ── 6. Rows ──────────────────────────────────────────────────────────────────

// Rows are the parsed rows plus what could not be read: a bad line is a problem that
// Validate turns into one failed parse verdict, never an error that stops the run.
type Rows struct {
	Items    []*Object
	Problems []string
}

// pyIsSpace is Python's str.isspace for one code point.
func pyIsSpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
}

// splitLines is Python's str.splitlines: every line break Python knows, \r\n as one.
func splitLines(s string) []string {
	var out []string
	start := 0
	for i, r := range s {
		switch r {
		case '\n', '\r', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
		default:
			continue
		}
		if r == '\n' && i > 0 && s[i-1] == '\r' {
			start = i + 1
			continue
		}
		out = append(out, s[start:i])
		start = i + utf8.RuneLen(r)
	}
	if start < len(s) {
		out = append(out, s[start:])
	}
	return out
}

// MaxDepth is ruling D16's nesting cap, the same number in contract.py.
const MaxDepth = 64

func scanHex4(s string, i int) (int, bool) {
	if i+4 > len(s) {
		return 0, false
	}
	n := 0
	for _, c := range []byte(s[i : i+4]) {
		switch {
		case c >= '0' && c <= '9':
			n = n*16 + int(c-'0')
		case c >= 'a' && c <= 'f':
			n = n*16 + int(c-'a') + 10
		case c >= 'A' && c <= 'F':
			n = n*16 + int(c-'A') + 10
		default:
			return 0, false
		}
	}
	return n, true
}

// scan finds, before decoding, what contract.py's _scan finds (ruling D16): brackets
// outside strings deeper than MaxDepth (an array's own bracket does not count), or a
// surrogate escape that is not a high one followed by a low one. The decoder would turn
// that into U+FFFD, so it is caught here. It returns the row (the array item, or 1).
func scan(text string, array bool) (int, string, bool) {
	depth, row, i, n := 0, 1, 0, len(text)
	limit := MaxDepth
	if array {
		limit++
	}
	for i < n {
		switch c := text[i]; {
		case c == '"':
			i++
			for i < n && text[i] != '"' {
				if text[i] != '\\' {
					i++
					continue
				}
				hi, ok := 0, false
				if i+1 < n && text[i+1] == 'u' {
					hi, ok = scanHex4(text, i+2)
				}
				if ok && hi >= 0xd800 && hi <= 0xdfff {
					lo, lok := 0, false
					if hi < 0xdc00 && strings.HasPrefix(text[min(i+6, n):], `\u`) {
						lo, lok = scanHex4(text, i+8)
					}
					if !lok || lo < 0xdc00 || lo > 0xdfff {
						return row, "invalid unicode escape", true
					}
					i += 12
					continue
				}
				i += 2
			}
		case c == '[' || c == '{':
			depth++
			if depth > limit {
				return row, fmt.Sprintf("nesting deeper than %d", MaxDepth), true
			}
		case c == ']' || c == '}':
			depth--
		case c == ',' && array && depth == 1:
			row++
		}
		i++
	}
	return 0, "", false
}

// ParseRows reads dataset text as contract.py's parse_rows does: a JSON array, one JSON
// object (one row), or JSONL. Row numbers count parsed rows from 1; a problem found before
// decoding names its row: the item in an array, the line in JSONL.
func ParseRows(text string) Rows {
	stripped := strings.TrimFunc(text, pyIsSpace)
	if stripped == "" {
		return Rows{}
	}
	isArray := strings.HasPrefix(stripped, "[")
	if isArray {
		if row, problem, bad := scan(stripped, true); bad {
			return Rows{Problems: []string{fmt.Sprintf("row %d: %s", row, problem)}}
		}
	}
	_, _, bad := scan(stripped, false)
	if isArray || !bad {
		if whole, err := ParseJSON(stripped); err == nil {
			switch x := whole.(type) {
			case *Object:
				return Rows{Items: []*Object{x}}
			case []any:
				var rows Rows
				for i, item := range x {
					if o, ok := item.(*Object); ok {
						rows.Items = append(rows.Items, o)
					} else {
						rows.Problems = append(rows.Problems, fmt.Sprintf("item %d: expected object, got %s", i+1, jsonType(item)))
					}
				}
				return rows
			}
			return Rows{Problems: []string{"expected object or array, got " + jsonType(whole)}}
		}
	}
	if isArray {
		return Rows{Problems: []string{"invalid JSON array"}}
	}
	var rows Rows
	for n, line := range splitLines(text) {
		if strings.TrimFunc(line, pyIsSpace) == "" {
			continue
		}
		if _, problem, bad := scan(line, false); bad {
			rows.Problems = append(rows.Problems, fmt.Sprintf("row %d: %s", n+1, problem))
			continue
		}
		v, err := ParseJSON(line)
		if err != nil {
			rows.Problems = append(rows.Problems, fmt.Sprintf("line %d: invalid JSON", n+1))
		} else if o, ok := v.(*Object); ok {
			rows.Items = append(rows.Items, o)
		} else {
			rows.Problems = append(rows.Problems, fmt.Sprintf("line %d: expected object, got %s", n+1, jsonType(v)))
		}
	}
	return rows
}

// ReadRows reads a dataset file; Python reads text with universal newlines, so \r\n and
// \r become \n first.
func ReadRows(path string) Rows {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Rows{Problems: []string{"dataset file not found"}}
	}
	if !utf8.Valid(raw) {
		return Rows{Problems: []string{"dataset is not valid UTF-8"}}
	}
	text := strings.ReplaceAll(string(raw), "\r\n", "\n")
	return ParseRows(strings.ReplaceAll(text, "\r", "\n"))
}

// ── 7. Rules ─────────────────────────────────────────────────────────────────

// Verdict is one rule's result for one field (or for the dataset when Field is nil).
type Verdict struct {
	Rule   string  `json:"rule"`
	Field  *string `json:"field"`
	OK     bool    `json:"ok"`
	Detail string  `json:"detail"`
}

func verdict(rule string, field *string, ok bool, detail string) Verdict {
	return Verdict{rule, field, ok, detail}
}

func plural(n int) string {
	if n == 1 {
		return "1 row"
	}
	return fmt.Sprintf("%d rows", n)
}

// first is ok with the pass text, or the first failure and how many more there were.
func first(fails []string, okDetail string) (bool, string) {
	if len(fails) == 0 {
		return true, okDetail
	}
	if len(fails) > 1 {
		return false, fmt.Sprintf("%s (+%d more)", fails[0], len(fails)-1)
	}
	return false, fails[0]
}

func present(r *Object, name string) bool {
	return r.Vals[name] != nil
}

func fieldRules(f *Field, rows []*Object) []Verdict {
	name := &f.Name
	var out []Verdict
	if f.Required {
		var fails []string
		for i, r := range rows {
			if !present(r, f.Name) {
				fails = append(fails, fmt.Sprintf("row %d: missing", i+1))
			}
		}
		ok, d := first(fails, "present in "+plural(len(rows)))
		out = append(out, verdict("required", name, ok, d))
	}
	var fails []string
	for i, r := range rows {
		if v := r.Vals[f.Name]; v != nil && !typeOK(v, f.Type) {
			fails = append(fails, fmt.Sprintf("row %d: expected %s, got %s", i+1, f.Type, jsonType(v)))
		}
	}
	ok, d := first(fails, "all values are "+f.Type)
	out = append(out, verdict("type", name, ok, d))
	if f.HasEnum {
		allowed := map[string]bool{}
		for _, e := range f.Enum {
			allowed[Show(e)] = true
		}
		fails = nil
		for i, r := range rows {
			if v := r.Vals[f.Name]; v != nil && !allowed[Show(v)] {
				fails = append(fails, fmt.Sprintf("row %d: %s not in enum %s", i+1, Show(v), Show(f.Enum)))
			}
		}
		ok, d := first(fails, "all values in enum")
		out = append(out, verdict("enum", name, ok, d))
	}
	if f.HasPattern {
		fails = nil
		for i, r := range rows {
			if s, isStr := r.Vals[f.Name].(string); isStr && !f.rx.MatchString(s) {
				fails = append(fails, fmt.Sprintf("row %d: %s does not match %s", i+1, Show(s), f.Pattern))
			}
		}
		ok, d := first(fails, "all values match")
		out = append(out, verdict("pattern", name, ok, d))
	}
	return out
}

// extraFields reports fields the contract does not mention: never failed (an additive change).
func extraFields(c *Contract, rows []*Object) []Verdict {
	declared := map[string]bool{}
	for _, f := range c.Fields {
		declared[f.Name] = true
	}
	counts := map[string]int{}
	var order []string
	for _, r := range rows {
		for _, k := range r.Keys {
			if !declared[k] {
				if counts[k] == 0 {
					order = append(order, k)
				}
				counts[k]++
			}
		}
	}
	var out []Verdict
	for _, k := range order {
		out = append(out, verdict("extra_field", &k, true, "not in schema ("+plural(counts[k])+")"))
	}
	return out
}

func qualityVerdicts(c *Contract, rows []*Object) []Verdict {
	var out []Verdict
	if c.HasRowCount {
		n := int64(len(rows))
		var bounds []string
		if c.RowMin != nil {
			bounds = append(bounds, "min "+c.RowMin.String())
		}
		if c.RowMax != nil {
			bounds = append(bounds, "max "+c.RowMax.String())
		}
		switch {
		case c.RowMin != nil && (!c.RowMin.IsInt64() || n < c.RowMin.Int64()):
			out = append(out, verdict("row_count", nil, false, plural(len(rows))+", below min "+c.RowMin.String()))
		case c.RowMax != nil && c.RowMax.IsInt64() && n > c.RowMax.Int64():
			out = append(out, verdict("row_count", nil, false, plural(len(rows))+", above max "+c.RowMax.String()))
		default:
			out = append(out, verdict("row_count", nil, true, plural(len(rows))+" ("+strings.Join(bounds, ", ")+")"))
		}
	}
	if len(c.Unique) > 0 {
		// ruling D8: the listed fields are ONE composite key; a row lacking any of them fails
		seen := map[string]int{}
		var fails []string
	rowLoop:
		for i, r := range rows {
			parts := make([]string, len(c.Unique))
			for k, name := range c.Unique {
				if !present(r, name) {
					fails = append(fails, fmt.Sprintf("row %d: missing key field %s", i+1, name))
					continue rowLoop
				}
				parts[k] = Show(r.Vals[name])
			}
			key := strings.Join(parts, "\x00")
			if j, dup := seen[key]; dup {
				fails = append(fails, fmt.Sprintf("row %d: duplicate of row %d", i+1, j))
			} else {
				seen[key] = i + 1
			}
		}
		ok, d := first(fails, plural(len(rows))+", no duplicates")
		name := strings.Join(c.Unique, ",")
		out = append(out, verdict("unique", &name, ok, d))
	}
	for _, name := range c.EnumCoverage {
		seen := map[string]bool{}
		for _, r := range rows {
			if present(r, name) {
				seen[Show(r.Vals[name])] = true
			}
		}
		var f *Field
		for i := range c.Fields {
			if c.Fields[i].Name == name {
				f = &c.Fields[i]
			}
		}
		var missing []string
		for _, e := range f.Enum {
			if !seen[Show(e)] {
				missing = append(missing, Show(e))
			}
		}
		if len(missing) > 0 {
			out = append(out, verdict("enum_coverage", &name, false, "missing values: "+strings.Join(missing, ", ")))
		} else {
			out = append(out, verdict("enum_coverage", &name, true, fmt.Sprintf("all %d values appear", len(f.Enum))))
		}
	}
	return out
}

func freshness(c *Contract, age *float64) Verdict {
	limit := c.MaxAgeDays
	switch {
	case age == nil:
		return verdict("freshness", nil, true, "unknown: no git date")
	case *age > limit:
		return verdict("freshness", nil, false, fmt.Sprintf("%s days old, over max %s", num(*age), num(limit)))
	}
	return verdict("freshness", nil, true, fmt.Sprintf("%s days old (max %s)", num(*age), num(limit)))
}

// ── 8. Verdicts ──────────────────────────────────────────────────────────────

// Validate checks rows against a loaded contract: one verdict per rule and field, sorted
// by (rule, field). ageDays is the file age the caller computed; nil means unknown.
func Validate(c *Contract, rows Rows, ageDays *float64) []Verdict {
	var out []Verdict
	if len(rows.Problems) > 0 {
		ok, d := first(rows.Problems, "")
		out = append(out, verdict("parse", nil, ok, d))
	}
	for i := range c.Fields {
		out = append(out, fieldRules(&c.Fields[i], rows.Items)...)
	}
	out = append(out, extraFields(c, rows.Items)...)
	out = append(out, qualityVerdicts(c, rows.Items)...)
	out = append(out, freshness(c, ageDays))
	if len(rows.Items) == 0 {
		// nothing to check is not a failure; row_count alone says whether empty is allowed
		for i := range out {
			if r := out[i].Rule; r != "row_count" && r != "freshness" && r != "parse" {
				out[i].OK, out[i].Detail = true, "no rows"
			}
		}
	}
	key := func(v Verdict) string {
		if v.Field == nil {
			return ""
		}
		return *v.Field
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Rule != out[j].Rule {
			return out[i].Rule < out[j].Rule
		}
		return key(out[i]) < key(out[j])
	})
	return out
}

// FormatLines prints `OK  rule field: detail` per verdict (a nil field prints as -),
// then the summary line.
func FormatLines(verdicts []Verdict) []string {
	var lines []string
	bad := 0
	for _, v := range verdicts {
		mark := "OK "
		if !v.OK {
			mark, bad = "FAIL", bad+1
		}
		field := "-"
		if v.Field != nil {
			field = *v.Field
		}
		lines = append(lines, fmt.Sprintf("%s %s %s: %s", mark, v.Rule, field, v.Detail))
	}
	return append(lines, fmt.Sprintf("verdicts: %d ok, %d failed", len(verdicts)-bad, bad))
}
