// validate — the Go twin of data/python/contract.py: same verdicts, same lines, same exit code.
//
// Run (from data/go):
//
//	go run ./validate <stem.contract.json> <dataset> [-age-days N]
//
// The contract is the JSON export (contract.py --export <stem.yaml>); the YAML is the
// source. Exit 1 when any verdict failed, 2 on a usage error. Free and offline.
package main

import (
	"flag"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
)

// ── 9. Command line ──────────────────────────────────────────────────────────

// ageFlag parses like Python's float() and refuses a negative age, as contract.py does.
type ageFlag struct{ v *float64 }

func (a *ageFlag) String() string { return "" }

func (a *ageFlag) Set(s string) error {
	t := strings.TrimSpace(s)
	x, err := strconv.ParseFloat(t, 64)
	if err != nil || strings.ContainsAny(t, "xX_") {
		return fmt.Errorf("-age-days needs a number, got %q", s)
	}
	if x < 0 {
		return fmt.Errorf("-age-days must not be negative")
	}
	a.v = &x
	return nil
}

// run is main without the exit: flags may come before, between or after the two paths.
func run(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("validate", flag.ContinueOnError)
	fs.SetOutput(stderr)
	age := &ageFlag{}
	fs.Var(age, "age-days", "age of the dataset file in days (from git)")
	var paths []string
	for {
		if err := fs.Parse(args); err != nil {
			return 2
		}
		if fs.NArg() == 0 {
			break
		}
		paths, args = append(paths, fs.Arg(0)), fs.Args()[1:]
	}
	if len(paths) != 2 {
		fmt.Fprintln(stderr, "usage: validate <contract.contract.json> <dataset> [-age-days N]")
		return 2
	}
	var verdicts []Verdict
	if c, err := LoadContractFile(paths[0]); err != nil {
		verdicts = []Verdict{verdict("contract", nil, false, err.Error())}
	} else {
		verdicts = Validate(c, ReadRows(paths[1]), age.v)
	}
	fmt.Fprintln(stdout, strings.Join(FormatLines(verdicts), "\n"))
	for _, v := range verdicts {
		if !v.OK {
			return 1
		}
	}
	return 0
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}
