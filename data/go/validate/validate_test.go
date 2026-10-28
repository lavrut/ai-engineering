package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The Go twin against the shared fixtures: every case in data/fixtures/cases.jsonl must give
// exactly the verdicts the Python validator gives (hand-written in gen_cases.py).

const casesFile = "../../fixtures/cases.jsonl"

type fixture struct {
	name    string
	raw     map[string]json.RawMessage
	value   *Object
	expect  []Verdict
	ageDays *float64
}

func loadCases(t *testing.T) []fixture {
	t.Helper()
	text, err := os.ReadFile(casesFile)
	if err != nil {
		t.Fatal(err)
	}
	var out []fixture
	for _, line := range strings.Split(strings.TrimRight(string(text), "\n"), "\n") {
		var raw map[string]json.RawMessage
		if err := json.Unmarshal([]byte(line), &raw); err != nil {
			t.Fatal(err)
		}
		v, err := ParseJSON(line)
		if err != nil {
			t.Fatal(err)
		}
		obj := v.(*Object)
		var f fixture
		f.raw, f.value = raw, obj
		json.Unmarshal(raw["name"], &f.name)
		if err := json.Unmarshal(raw["expect"], &f.expect); err != nil {
			t.Fatal(err)
		}
		json.Unmarshal(raw["age_days"], &f.ageDays)
		out = append(out, f)
	}
	return out
}

// rowsOf reads a case's dataset the way a file is read, or takes its rows list as is.
func rowsOf(t *testing.T, f fixture) Rows {
	t.Helper()
	if ds, ok := f.value.Get("dataset"); ok {
		return ParseRows(ds.(string))
	}
	list, _ := f.value.Get("rows")
	var rows Rows
	for _, r := range list.([]any) {
		rows.Items = append(rows.Items, r.(*Object))
	}
	return rows
}

func TestCasesMatchFixtures(t *testing.T) {
	cases := loadCases(t)
	if len(cases) < 18 {
		t.Fatalf("expected at least 18 cases, got %d", len(cases))
	}
	seen := map[string]bool{}
	for _, f := range cases {
		t.Run(f.name, func(t *testing.T) {
			if seen[f.name] {
				t.Fatalf("duplicate case name %s", f.name)
			}
			seen[f.name] = true
			cv, _ := f.value.Get("contract")
			c, err := LoadContract(cv)
			if err != nil {
				t.Fatalf("contract: %v", err)
			}
			got, _ := json.Marshal(Validate(c, rowsOf(t, f), f.ageDays))
			want, _ := json.Marshal(f.expect)
			if !bytes.Equal(got, want) {
				t.Errorf("verdicts differ\n got: %s\nwant: %s", got, want)
			}
		})
	}
}

func TestShowMatchesPythonJSON(t *testing.T) {
	for src, want := range map[string]string{
		`1`: `1`, `-0`: `0`, `1.0`: `1.0`, `2.50`: `2.5`, `1e5`: `100000.0`, `1e16`: `1e+16`,
		`1.5e-7`: `1.5e-07`, `0.0001`: `0.0001`, `-0.0`: `-0.0`, `1e400`: `Infinity`, `NaN`: `NaN`,
		`123456789012345678901234567890`:       `123456789012345678901234567890`,
		`"a\"b\\c\né \u0001"`:                  "\"a\\\"b\\\\c\\né \\u0001\"",
		`{"b": [1, {}], "a": null, "b": true}`: `{"b": true, "a": null}`,
		`[]`:                                   `[]`,
	} {
		v, err := ParseJSON(src)
		if err != nil {
			t.Fatalf("%s: %v", src, err)
		}
		if got := Show(v); got != want {
			t.Errorf("Show(%s) = %s, want %s", src, got, want)
		}
	}
	for _, bad := range []string{`01`, `1.`, `-`, `{"a":1,}`, `"a` + "\t" + `"`, `[1] x`, `nan`} {
		if _, err := ParseJSON(bad); err == nil {
			t.Errorf("ParseJSON(%q) should fail", bad)
		}
	}
}

func TestNumMatchesPercentG(t *testing.T) {
	for x, want := range map[float64]string{
		10: "10", 11.5: "11.5", 100000: "100000", 1e6: "1e+06", 1234567: "1.23457e+06",
		0.0001: "0.0001", 0.00001: "1e-05", 0.1 + 0.2: "0.3", 3: "3",
	} {
		if got := num(x); got != want {
			t.Errorf("num(%v) = %s, want %s", x, got, want)
		}
	}
}

func TestContractErrorsMatchPython(t *testing.T) {
	base := `{"info": {"id": "x", "version": 1, "status": "active", "owner": "o", "description": "d"},
	 "schema": {"fields": [{"name": "id", "type": "string", "required": true, "meaning": "m", "pattern": %s}]},
	 "quality": {}, "servicing": {"freshness": {"max_age_days": 3}}, "consumers": []}`
	for pat, want := range map[string]string{
		`"(?=x)"`:    "schema.fields[0].pattern (id) uses syntax Go RE2 lacks: lookahead (?=",
		`"(a)\\1"`:   `schema.fields[0].pattern (id) uses syntax Go RE2 lacks: backreference \1`,
		`"a++"`:      "schema.fields[0].pattern (id) uses syntax Go RE2 lacks: possessive quantifier ++",
		`"\\Aab"`:    `schema.fields[0].pattern (id) uses syntax Go RE2 lacks: \A anchor`,
		`3`:          "schema.fields[0].pattern must be a string",
		`"[*+]+\\+"`: "",
	} {
		v, err := ParseJSON(strings.Replace(base, "%s", pat, 1))
		if err != nil {
			t.Fatal(err)
		}
		_, err = LoadContract(v)
		got := ""
		if err != nil {
			got = err.Error()
		}
		if got != want {
			t.Errorf("pattern %s: got %q, want %q", pat, got, want)
		}
	}
	v, _ := ParseJSON(`{"info": 3}`)
	if _, err := LoadContract(v); err == nil || err.Error() != "missing section: schema" {
		t.Errorf("got %v", err)
	}
}

// TestCLIOutputAndExit builds the binary and runs it on one passing and one failing dataset.
func TestCLIOutputAndExit(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "validate")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("build: %v\n%s", err, out)
	}
	contract := filepath.Join(dir, "c.contract.json")
	os.WriteFile(contract, []byte(`{"consumers": [], "info": {"description": "d", "id": "c", "owner": "o",
	 "status": "active", "version": "1"}, "quality": {"row_count": {"min": 1}},
	 "schema": {"fields": [{"meaning": "the id", "name": "id", "required": true, "type": "integer"}]},
	 "servicing": {"freshness": {"max_age_days": 30}}}`), 0o644)
	ok := filepath.Join(dir, "ok.jsonl")
	os.WriteFile(ok, []byte("{\"id\": 1}\n"), 0o644)
	bad := filepath.Join(dir, "bad.jsonl")
	os.WriteFile(bad, []byte("{\"id\": \"x\"}\n"), 0o644)

	run := func(args ...string) (string, int) {
		cmd := exec.Command(bin, args...)
		var out bytes.Buffer
		cmd.Stdout = &out
		err := cmd.Run()
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			return out.String(), ee.ExitCode()
		}
		if err != nil {
			t.Fatal(err)
		}
		return out.String(), 0
	}
	out, code := run(contract, ok, "-age-days", "3")
	want := "OK  freshness -: 3 days old (max 30)\n" +
		"OK  required id: present in 1 row\n" +
		"OK  row_count -: 1 row (min 1)\n" +
		"OK  type id: all values are integer\n" +
		"verdicts: 4 ok, 0 failed\n"
	if out != want || code != 0 {
		t.Errorf("passing run: exit %d\n%s", code, out)
	}
	out, code = run(contract, bad)
	if code != 1 || !strings.Contains(out, "FAIL type id: row 1: expected integer, got string\n") ||
		!strings.HasSuffix(out, "verdicts: 3 ok, 1 failed\n") {
		t.Errorf("failing run: exit %d\n%s", code, out)
	}
	broken := filepath.Join(dir, "broken.contract.json")
	os.WriteFile(broken, []byte(`{"info": 3}`), 0o644)
	out, code = run(broken, ok)
	if code != 1 || out != "FAIL contract -: missing section: schema\nverdicts: 0 ok, 1 failed\n" {
		t.Errorf("broken contract: exit %d\n%s", code, out)
	}
	if _, code = run(contract, ok, "--age-days", "-3"); code != 2 {
		t.Errorf("negative age: exit %d, want 2", code)
	}
}
