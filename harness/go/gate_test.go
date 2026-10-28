package main

import (
	"bufio"
	"context"
	"errors"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/anthropics/anthropic-sdk-go"
)

// The money gate in the REPL (section 9): the estimate, y/N, --yes and --max-cost. No API calls:
// every test passes a fake in place of callModel, with a dummy key in the environment.

const estimate = "Estimate: typical $0.10 a task (4 calls of ~4,000 input and ~400 output tokens); " +
	"worst case $23.00 (MAX_TURNS 25 calls of 150,000 input and 16,000 output tokens, " +
	"plus any compaction summaries), at $4.00 / $20.00 per million tokens."

// capture runs f and returns what it printed to standard output.
func capture(t *testing.T, f func()) string {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	old := os.Stdout
	os.Stdout = w
	done := make(chan string)
	go func() { b, _ := io.ReadAll(r); done <- string(b) }()
	f()
	w.Close()
	os.Stdout = old
	return <-done
}

// runRepl drives repl with the given input lines, a terminal or not, and a fake model.
func runRepl(t *testing.T, input string, tty bool, args ...string) (code int, calls []anthropic.MessageNewParams, out string) {
	t.Helper()
	setup(t)
	t.Setenv("ANTHROPIC_API_KEY", "test-key")
	oldIn, oldTTY := stdin, isTerminal
	stdin = bufio.NewReader(strings.NewReader(input))
	isTerminal = func() bool { return tty }
	t.Cleanup(func() { stdin, isTerminal = oldIn, oldTTY })
	fake := func(ctx context.Context, p anthropic.MessageNewParams) (*anthropic.Message, error) {
		calls = append(calls, p)
		return reply(t, "end_turn", textBlock("done"), 100), nil
	}
	out = capture(t, func() { code = repl(args, fake) })
	return code, calls, out
}

func TestPricesMatchTheLLMsLab(t *testing.T) {
	lab, err := os.ReadFile(filepath.Join("..", "..", "llms", "python", "triage_lab.py"))
	if err != nil {
		t.Fatal(err)
	}
	m := regexp.MustCompile(`"claude-opus-5-5": \(([\d.]+), ([\d.]+)\)`).FindStringSubmatch(string(lab))
	if model != "claude-opus-5-5" || m == nil || m[1] != "4.00" || m[2] != "20.00" ||
		pricePerMTok != [2]float64{4.00, 20.00} {
		t.Fatalf("prices %v, lab %v", pricePerMTok, m)
	}
}

func TestEstimateLines(t *testing.T) {
	got := estimateLines(&ledger{cap: 10})
	want := []string{estimate, "Spent $0.0000 this session; --max-cost stops it before $10.00."}
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("got\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
}

func TestYOnATerminalRunsTheTask(t *testing.T) {
	code, calls, out := runRepl(t, "list the files\ny\nexit\n", true)
	if code != 0 || len(calls) != 1 {
		t.Fatalf("code %d, %d calls\n%s", code, len(calls), out)
	}
	for _, want := range []string{estimate, "Run it? [y/N] ", "agent > done", "[$0.0006 this task, $0.0006 this session]"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in\n%s", want, out)
		}
	}
}

func TestAnythingButYIsNo(t *testing.T) {
	for _, answer := range []string{"", "n", "yes", "Y please"} {
		_, calls, out := runRepl(t, "list the files\n"+answer+"\nexit\n", true)
		if len(calls) != 0 || !strings.Contains(out, "Not run. Nothing was spent.") {
			t.Errorf("answer %q: %d calls\n%s", answer, len(calls), out)
		}
	}
	_, calls, out := runRepl(t, "list the files\n", true) // end of input at the question
	if len(calls) != 0 || !strings.Contains(out, "Not run. Nothing was spent.") {
		t.Errorf("EOF: %d calls\n%s", len(calls), out)
	}
}

func TestNoTerminalIsNo(t *testing.T) {
	_, calls, out := runRepl(t, "list the files\ny\nexit\n", false)
	if len(calls) != 0 || !strings.Contains(out, "Not run: no terminal to ask. Nothing was spent.") {
		t.Fatalf("%d calls\n%s", len(calls), out)
	}
}

func TestYesFlagRunsAScriptedTask(t *testing.T) {
	_, calls, out := runRepl(t, "list the files\nexit\n", false, "--yes")
	if len(calls) != 1 || !strings.Contains(out, estimate) || !strings.Contains(out, "agent > done") {
		t.Fatalf("%d calls\n%s", len(calls), out)
	}
}

func TestMaxCostStopsBeforeACall(t *testing.T) {
	// A worst-case call is at least 16,000 output tokens x $20 / million = $0.32, so a $0.30 cap stops the first one.
	_, calls, out := runRepl(t, "list the files\ny\nexit\n", true, "--max-cost", "0.30")
	re := regexp.MustCompile(`Stopped: spent \$0\.0000; the next call could cost up to \$0\.3\d{3}, over --max-cost 0\.30`)
	if len(calls) != 0 || !re.MatchString(out) {
		t.Fatalf("%d calls\n%s", len(calls), out)
	}
}

func TestCapCountsWhatWasSpent(t *testing.T) {
	setup(t)
	l := &ledger{cap: 0.70} // room for one call ($0.4002 spent), not for a second worst case ($0.32+)
	n := 0
	fake := func(context.Context, anthropic.MessageNewParams) (*anthropic.Message, error) {
		n++
		return reply(t, "tool_use", listCall, 100000), nil
	}
	_, err := runAgent(context.Background(), "hi", metered(fake, l), yes)
	var limit *costLimit
	if !errors.As(err, &limit) || n != 1 || math.Abs(l.spent-0.4002) > 1e-9 {
		t.Fatalf("err %v, %d calls, spent %v", err, n, l.spent)
	}
}

func TestSummaryCallIsSizedWithItsOwnMaxTokens(t *testing.T) {
	setup(t)
	l := &ledger{cap: 0.30} // under one 16,000-token call, over one 4,000-token summary
	gated := metered(func(context.Context, anthropic.MessageNewParams) (*anthropic.Message, error) {
		return reply(t, "end_turn", textBlock("s"), 100), nil
	}, l)
	msgs := []anthropic.MessageParam{anthropic.NewUserMessage(anthropic.NewTextBlock("hi"))}
	if _, err := gated(context.Background(), anthropic.MessageNewParams{Messages: msgs, MaxTokens: 4000}); err != nil {
		t.Fatal(err)
	}
	if _, err := gated(context.Background(), anthropic.MessageNewParams{Messages: msgs}); err == nil {
		t.Fatal("a full-size call should be over the cap")
	}
}

func TestMissingKeyIsAMessage(t *testing.T) {
	setup(t)
	t.Setenv("ANTHROPIC_API_KEY", "")
	var code int
	out := capture(t, func() { code = repl(nil, nil) })
	if code != 1 || !strings.Contains(out, "Set ANTHROPIC_API_KEY first (lesson 2). Nothing was called.") {
		t.Fatalf("code %d\n%s", code, out)
	}
}

func TestEOFAtThePermissionPromptIsNo(t *testing.T) {
	setup(t)
	old := stdin
	stdin = bufio.NewReader(strings.NewReader(""))
	t.Cleanup(func() { stdin = old })
	var ok bool
	capture(t, func() { ok = approve("write_file", `{"path":"x"}`) })
	if ok {
		t.Fatal("end of input must deny")
	}
}
