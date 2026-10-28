package live

// The real MCP Tools against the real servers over stdio: mcp/python/read_server.py and the lumen-ops launcher
// agents/desk/python/ops_stdio.py, run by .venv-agents' Python. No network, no API. If that Python is missing,
// every test here is skipped with the reason (never a silent pass).

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"testing"

	patterns "lumen-agents"
)

func python(t *testing.T) string {
	t.Helper()
	p := filepath.Join(patterns.Root(), ".venv-agents", "bin", "python")
	if _, err := os.Stat(p); err != nil {
		t.Skipf("MCP TOOLS TESTS SKIPPED: %s not found (create .venv-agents first)", p)
	}
	return p
}

func open(t *testing.T, servers []string, approve, elicit string) (*Tools, *TraceLog, string) {
	t.Helper()
	labels := filepath.Join(t.TempDir(), "labels.jsonl")
	log := &TraceLog{}
	tools, err := OpenTools(ToolsConfig{Python: python(t), Root: patterns.Root(), Servers: servers, Approve: approve,
		Elicit: elicit, Labels: labels, Agent: "single", Log: log})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = tools.Close() })
	return tools, log, labels
}

func rows(t *testing.T, path string) []map[string]any {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	var out []map[string]any
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var m map[string]any
		_ = json.Unmarshal(sc.Bytes(), &m)
		out = append(out, m)
	}
	return out
}

func TestToolsAreExactlyTheDeskTools(t *testing.T) {
	tools, _, _ := open(t, []string{"read", "ops"}, "decline", "accept")
	var names []string
	for n, s := range tools.Schemas() {
		names = append(names, n)
		if s["name"] != n || s["input_schema"] == nil {
			t.Fatalf("schema for %s: %v", n, s)
		}
	}
	sort.Strings(names)
	if want := []string{"classify_complaint", "get_ticket", "label_ticket", "search_tickets"}; !slices.Equal(names, want) {
		t.Fatalf("tools %v, want %v", names, want)
	}
}

func TestReadToolResultIsPythonStyleAndTaints(t *testing.T) {
	tools, log, _ := open(t, []string{"read"}, "decline", "accept")
	if tools.Untrusted() {
		t.Fatal("a fresh session must start clear")
	}
	out, isErr, err := tools.Run("get_ticket", map[string]any{"id": "t001"})
	if err != nil || isErr {
		t.Fatalf("get_ticket: %q %v %v", out, isErr, err)
	}
	if !strings.HasPrefix(out, "{") || !strings.Contains(out, `"id": "t001"`) {
		t.Fatalf("structured result must be pyJSON (sorted keys, \", \" and \": \"): %q", out)
	}
	if !tools.Untrusted() {
		t.Fatal("get_ticket returns ticket text: the session must be marked untrusted (taints)")
	}
	if recs := log.Records(); len(recs) != 1 || recs[0].Kind != "tool" || recs[0].Name != "get_ticket" || recs[0].Agent != "single" {
		t.Fatalf("trace %+v", recs)
	}
	if out, isErr, _ := tools.Run("label_ticket", map[string]any{"id": "t001", "label": "billing"}); !isErr || out != "Unknown tool: label_ticket" {
		t.Fatalf("lumen-ops is not open here: %q %v", out, isErr)
	}
}

func TestMarkedSessionWriteNeedsTheHuman(t *testing.T) {
	tools, _, labels := open(t, []string{"read", "ops"}, "decline", "accept")
	tools.MarkUntrusted() // R24: the ticket text is in the first message
	out, isErr, err := tools.Run("label_ticket", map[string]any{"id": "t001", "label": "billing"})
	if err != nil || !isErr || out != "The user denied this action." {
		t.Fatalf("label after the mark with approve=decline: %q %v %v", out, isErr, err)
	}
	if r := rows(t, labels); len(r) != 0 {
		t.Fatalf("nothing may be written, got %v", r)
	}
}

func TestUnmarkedWriteIsAllowedByTheGateAndConfirmedByTheServer(t *testing.T) {
	tools, _, labels := open(t, []string{"read", "ops"}, "decline", "accept")
	out, isErr, err := tools.Run("label_ticket", map[string]any{"id": "t001", "label": "billing"})
	if err != nil || isErr {
		t.Fatalf("before any untrusted text the gate allows the write: %q %v %v", out, isErr, err)
	}
	if r := rows(t, labels); len(r) != 1 || r[0]["label"] != "billing" || r[0]["source"] != "agent" {
		t.Fatalf("one agent row, got %v", r)
	}
}

func TestHumanYesThenServerDeclineWritesNothing(t *testing.T) {
	tools, _, labels := open(t, []string{"read", "ops"}, "accept", "decline")
	tools.MarkUntrusted()
	out, isErr, err := tools.Run("label_ticket", map[string]any{"id": "t001", "label": "billing"})
	if err != nil || !isErr || !strings.HasPrefix(out, "ToolError: ") {
		t.Fatalf("the person said yes, lumen-ops' confirmation said no: %q %v %v", out, isErr, err)
	}
	if r := rows(t, labels); len(r) != 0 {
		t.Fatalf("nothing may be written, got %v", r)
	}
}

func TestHumanYesAndServerYesWrites(t *testing.T) {
	tools, _, labels := open(t, []string{"read", "ops"}, "accept", "accept")
	tools.MarkUntrusted()
	if out, isErr, err := tools.Run("label_ticket", map[string]any{"id": "t001", "label": "billing"}); err != nil || isErr {
		t.Fatalf("both said yes: %q %v %v", out, isErr, err)
	}
	if r := rows(t, labels); len(r) != 1 {
		t.Fatalf("one row, got %v", r)
	}
}

func TestUnknownToolNeverReachesAServer(t *testing.T) {
	tools, log, _ := open(t, []string{"read"}, "decline", "accept")
	out, isErr, err := tools.Run("write_file", map[string]any{"path": "x"})
	if err != nil || !isErr || out != "Unknown tool: write_file" {
		t.Fatalf("%q %v %v", out, isErr, err)
	}
	if len(log.Records()) != 1 { // logged as a tool record, as desk_core.run_tool does
		t.Fatalf("trace %+v", log.Records())
	}
}

func TestTaintsRule(t *testing.T) {
	cases := []struct {
		name       string
		structured any
		want       bool
	}{
		{"search_tickets", map[string]any{"results": []any{}}, true}, // a ticket tool always taints
		{"classify_complaint", nil, true},                            // no structured content: unchecked text
		{"classify_complaint", map[string]any{"label": "billing"}, false},
		{"classify_complaint", map[string]any{"x": []any{map[string]any{"untrusted_body": "hi"}}}, true},
	}
	for _, c := range cases {
		if got := taints(c.name, c.structured); got != c.want {
			t.Fatalf("taints(%s, %v) = %v, want %v", c.name, c.structured, got, c.want)
		}
	}
}
