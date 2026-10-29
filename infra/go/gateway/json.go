// json: JSON as Python reads and writes it. json.loads keeps a document's key order and an
// integer literal as an int (1.0 is a float); json.dumps writes floats by repr. These values
// carry both, so the twins read alike; pyRepr and pyStr give config.py's messages their values.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"unicode"
)

// Object is a JSON object in document order; a repeated key keeps its first place, last value.
type Object struct {
	Keys []string
	vals map[string]any
}

// Get and MustGet read a nil *Object as empty, as Python's (x or {}).get(k).
func (o *Object) Get(k string) (any, bool) {
	if o == nil {
		return nil, false
	}
	v, ok := o.vals[k]
	return v, ok
}

func (o *Object) MustGet(k string) any { v, _ := o.Get(k); return v }

func (o *Object) keys() []string {
	if o == nil {
		return nil
	}
	return o.Keys
}

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
