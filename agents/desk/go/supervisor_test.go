package patterns

// Focused checks of the supervisor's code gate beyond the contract: the ops tool is absent until the
// reviewer passes the current draft, the research worker never sees or reaches it, and every handoff is
// logged. The contract runner (patterns_test.go) holds the rest to Python's behaviour.

import (
	"encoding/json"
	"reflect"
	"slices"
	"testing"
)

func tu(name string, input map[string]any) map[string]any {
	return map[string]any{"tool_use": map[string]any{"name": name, "input": input}}
}

func ho(worker string) map[string]any {
	return tu("handoff", map[string]any{"worker": worker, "task": "go"})
}

func passJudge(failed ...string) string {
	v := map[string]any{}
	for _, id := range RubricIDs {
		v[id] = map[string]any{"pass": !slices.Contains(failed, id), "reason": "r"}
	}
	b, _ := json.Marshal(v)
	return string(b)
}

func supervisorTools(m *scriptedModel) [][]string {
	var out [][]string
	for _, r := range m.reqs {
		if r.Agent == "supervisor" {
			out = append(out, r.Tools)
		}
	}
	return out
}

func TestOpsToolAbsentUntilReviewPasses(t *testing.T) {
	label := tu("label_ticket", map[string]any{"id": "t001", "label": "billing"})
	m := &scriptedModel{script: []any{label, ho("triage"), `{"category": "billing", "priority": "high"}`,
		ho("drafter"), "Draft.", ho("reviewer"), passJudge(), label, "Done."}}
	tools := &stubTools{ops: true}
	run, err := Supervisor(loadTicket(t, "t001"), m, tools, loadPrompts(t))
	if err != nil {
		t.Fatal(err)
	}
	got := supervisorTools(m)
	for i, names := range got {
		has := slices.Contains(names, "label_ticket")
		if want := i >= 4; has != want { // calls 0-3: before the review passed; 4-5: after
			t.Fatalf("supervisor call %d tools %v: label_ticket offered=%v, want %v", i, names, has, want)
		}
	}
	if !slices.Equal(tools.names, []string{"label_ticket"}) {
		t.Fatalf("tools reached %v, want only the label after the review", tools.names)
	}
	out := run.Output.(SupervisorOutput)
	if !out.ToolCalls[0].IsError || out.ToolCalls[1].IsError {
		t.Fatalf("tool calls %+v", out.ToolCalls)
	}
	if out.Reply != "Draft." {
		t.Fatalf("reply %q, want the reviewed draft", out.Reply)
	}
}

// twoBlocks answers call n with a reviewer handoff and a label_ticket in one response.
type twoBlocks struct {
	*scriptedModel
	n int
}

func (m *twoBlocks) Call(req Request) (Reply, error) {
	if len(m.reqs) == m.n {
		m.reqs = append(m.reqs, req)
		return Reply{Content: []Block{
			{Type: "tool_use", ID: "a", Name: "handoff", Input: map[string]any{"worker": "reviewer", "task": "x"}},
			{Type: "tool_use", ID: "b", Name: "label_ticket", Input: map[string]any{"id": "t001", "label": "billing"}}}}, nil
	}
	return m.scriptedModel.Call(req)
}

func TestLabelNextToTheReviewIsRefused(t *testing.T) {
	m := &twoBlocks{&scriptedModel{script: []any{ho("drafter"), "Draft.", "placeholder", passJudge(), "Done."}}, 2}
	tools := &stubTools{ops: true}
	run, err := Supervisor(loadTicket(t, "t001"), m, tools, loadPrompts(t))
	if err != nil {
		t.Fatal(err)
	}
	out := run.Output.(SupervisorOutput)
	if !out.State.passed() || len(tools.names) != 0 || !out.ToolCalls[0].IsError {
		t.Fatalf("a label not offered in that request reached the tools: %v %+v", tools.names, out.ToolCalls)
	}
}

func TestResearchNeverSeesOrReachesOpsTools(t *testing.T) {
	m := &scriptedModel{script: []any{ho("research"), tu("label_ticket", map[string]any{"id": "t001", "label": "outage"}),
		tu("get_ticket", map[string]any{"id": "t061"}), "Read.", "Done."}}
	tools := &stubTools{ops: true}
	if _, err := Supervisor(loadTicket(t, "t061"), m, tools, loadPrompts(t)); err != nil {
		t.Fatal(err)
	}
	for _, r := range m.reqs {
		if r.Agent == "research" && (slices.Contains(r.Tools, "label_ticket") || !slices.Equal(r.Tools, ReadTools)) {
			t.Fatalf("research offered %v", r.Tools)
		}
	}
	if !slices.Equal(tools.names, []string{"get_ticket"}) {
		t.Fatalf("tools reached %v", tools.names)
	}
}

func TestNewDraftResetsReviewAndHandoffsAreLogged(t *testing.T) {
	m := &scriptedModel{script: []any{ho("drafter"), "D1.", ho("reviewer"), passJudge(), ho("drafter"), "D2.", ho("triage"), "not json", "Done."}}
	run, err := Supervisor(loadTicket(t, "t001"), m, &stubTools{ops: true}, loadPrompts(t))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, r := range run.Trace {
		if r.Kind == "handoff" && r.Agent == "supervisor" && r.Ms != nil {
			names = append(names, r.Name)
		}
	}
	want := []string{"drafter: draft", "reviewer: review", "drafter: draft, review", "triage: error"}
	if !slices.Equal(names, want) {
		t.Fatalf("trace %v, want %v", names, want)
	}
	if got := supervisorTools(m); slices.Contains(got[len(got)-1], "label_ticket") {
		t.Fatal("label_ticket still offered after a new draft")
	}
	if out := run.Output.(SupervisorOutput); out.Reply != "Done." || out.State.Review != nil {
		t.Fatalf("reply %q review %v", out.Reply, out.State.Review)
	}
}

type markingTools struct {
	stubTools
	marked bool
}

func (m *markingTools) MarkUntrusted() { m.marked = true }

func TestDesksMarkTheContextUntrusted(t *testing.T) {
	for _, desk := range []string{"single", "supervisor"} {
		tools := &markingTools{}
		m := byAgent(func(string) Reply {
			if !tools.marked {
				panic("a model call before the context was marked untrusted")
			}
			return Reply{Content: []Block{{Type: "text", Text: "ok"}}}
		})
		var err error
		if desk == "single" {
			_, err = SingleAgent(loadTicket(t, "t061"), m, tools)
		} else {
			_, err = Supervisor(loadTicket(t, "t061"), m, tools, loadPrompts(t))
		}
		if err != nil || !tools.marked {
			t.Fatalf("%s: err %v marked %v", desk, err, tools.marked)
		}
	}
}

func TestPyJSONIsPythonStyle(t *testing.T) {
	got := pyJSON(map[string]any{"b": []any{1, true, nil}, "a": "<x> & é"})
	if want := `{"a": "<x> & é", "b": [1, true, null]}`; got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}

// ── fix round 1: R28 mandatory mark, R27 binding, R29 stop records, reason text ─
type readOnlyTools struct{}

func (readOnlyTools) Run(string, map[string]any) (string, bool, error) { return "", false, nil }

func TestDeskToolsRequireMarkUntrusted(t *testing.T) {
	want := reflect.TypeOf((*DeskTools)(nil)).Elem()
	for name, fn := range map[string]any{"SingleAgent": SingleAgent, "Supervisor": Supervisor} {
		if got := reflect.TypeOf(fn).In(2); got != want {
			t.Fatalf("%s takes %v, want DeskTools (MarkUntrusted required)", name, got)
		}
	}
	var plain Tools = readOnlyTools{}
	if _, ok := plain.(DeskTools); ok {
		t.Fatal("a Tools without MarkUntrusted must not satisfy DeskTools")
	}
}

func TestLabelBoundToState(t *testing.T) {
	m := &scriptedModel{script: []any{ho("triage"), `{"category": "how_to", "priority": "low"}`, ho("drafter"), "Draft.",
		ho("reviewer"), passJudge(), tu("label_ticket", map[string]any{"id": "t001", "label": "outage"}),
		tu("label_ticket", map[string]any{"id": "t061", "label": "outage"}), "Done."}}
	tools := &stubTools{ops: true}
	run, err := Supervisor(loadTicket(t, "t061"), m, tools, loadPrompts(t))
	if err != nil {
		t.Fatal(err)
	}
	out := run.Output.(SupervisorOutput)
	if len(tools.names) != 0 || len(out.ToolCalls) != 2 || !out.ToolCalls[0].IsError || !out.ToolCalls[1].IsError {
		t.Fatalf("an unbound label reached the tools: %v %+v", tools.names, out.ToolCalls)
	}
}

func stopRecords(run DeskRun) []string {
	var out []string
	for _, r := range run.Trace {
		if r.Kind == "stop" {
			out = append(out, r.Agent+"/"+r.Name)
		}
	}
	return out
}

func repeat(e any, n int) []any {
	out := make([]any, n)
	for i := range out {
		out[i] = e
	}
	return out
}

func TestCapsEmitStopRecords(t *testing.T) {
	get := tu("get_ticket", map[string]any{"id": "t001"})
	single, err := SingleAgent(loadTicket(t, "t001"), &scriptedModel{script: repeat(get, MaxTurns)}, &stubTools{ops: true})
	if err != nil || !slices.Equal(stopRecords(single), []string{"single/max_turns"}) {
		t.Fatalf("single: %v %v", err, stopRecords(single))
	}
	script := append(append([]any{ho("research")}, repeat(get, MaxWorkerSteps)...), "Done.")
	res, err := Supervisor(loadTicket(t, "t001"), &scriptedModel{script: script}, &stubTools{ops: true}, loadPrompts(t))
	if err != nil || !slices.Equal(stopRecords(res), []string{"research/max_worker_steps"}) {
		t.Fatalf("research: %v %v", err, stopRecords(res))
	}
	sup, err := Supervisor(loadTicket(t, "t001"), &scriptedModel{script: repeat(ho("nobody"), MaxTurns)}, &stubTools{ops: true}, loadPrompts(t))
	if err != nil || !slices.Equal(stopRecords(sup), []string{"supervisor/max_turns"}) {
		t.Fatalf("supervisor: %v %v", err, stopRecords(sup))
	}
}

func TestSupervisorCapReplyIsDraftOnlyIfReviewPassed(t *testing.T) {
	for _, tc := range []struct {
		judge, want string
	}{{passJudge(), "Draft."}, {passJudge("tone"), Stopped}} {
		script := append([]any{ho("drafter"), "Draft.", ho("reviewer"), tc.judge}, repeat(ho("nobody"), MaxTurns-2)...)
		run, err := Supervisor(loadTicket(t, "t001"), &scriptedModel{script: script}, &stubTools{ops: true}, loadPrompts(t))
		if err != nil {
			t.Fatal(err)
		}
		if out := run.Output.(SupervisorOutput); out.Stop != "max_turns" || out.Reply != tc.want {
			t.Fatalf("stop %q reply %q, want %q", out.Stop, out.Reply, tc.want)
		}
	}
}

func TestReasonTextIsNormalised(t *testing.T) {
	for _, tc := range []struct {
		entry map[string]any
		want  string
	}{{map[string]any{"reason": nil}, ""}, {map[string]any{}, ""}, {map[string]any{"reason": true}, "true"},
		{map[string]any{"reason": false}, "false"}, {map[string]any{"reason": "ok"}, "ok"},
		{map[string]any{"reason": []any{float64(1), "a"}}, `[1, "a"]`}} {
		if got := reason(tc.entry); got != tc.want {
			t.Fatalf("reason(%v) = %q, want %q", tc.entry, got, tc.want)
		}
	}
}

// R30: a category change after a passed review clears it, so label_ticket leaves the tools and a label
// bound to the new category is refused; a re-triage to the same category keeps the review.
func TestCategoryChangeClearsReview(t *testing.T) {
	m := &scriptedModel{script: []any{ho("triage"), `{"category": "billing", "priority": "high"}`, ho("drafter"), "Draft.",
		ho("reviewer"), passJudge(), ho("triage"), `{"category": "account", "priority": "high"}`,
		tu("label_ticket", map[string]any{"id": "t001", "label": "account"}), "Done."}}
	tools := &stubTools{ops: true}
	run, err := Supervisor(loadTicket(t, "t001"), m, tools, loadPrompts(t))
	if err != nil {
		t.Fatal(err)
	}
	out := run.Output.(SupervisorOutput)
	last := out.Handoffs[len(out.Handoffs)-1].Changed
	if _, ok := last["review"]; !ok || last["review"] != nil || out.State.Review != nil {
		t.Fatalf("changed %v review %v, want the review cleared", last, out.State.Review)
	}
	if len(tools.names) != 0 || len(out.ToolCalls) != 1 || !out.ToolCalls[0].IsError || out.Reply != "Done." {
		t.Fatalf("a label after a re-triage reached the tools: %v %+v %q", tools.names, out.ToolCalls, out.Reply)
	}
	same := &scriptedModel{script: []any{ho("triage"), `{"category": "billing", "priority": "high"}`, ho("drafter"), "Draft.",
		ho("reviewer"), passJudge(), ho("triage"), `{"category": "billing", "priority": "low"}`, "Done."}}
	run, err = Supervisor(loadTicket(t, "t001"), same, &stubTools{ops: true}, loadPrompts(t))
	if err != nil {
		t.Fatal(err)
	}
	if out := run.Output.(SupervisorOutput); !out.State.passed() || len(out.Handoffs[len(out.Handoffs)-1].Changed) != 0 {
		t.Fatalf("a same-category re-triage changed %+v", out.Handoffs[len(out.Handoffs)-1])
	}
}

// R37: an unknown worker, a non-string worker and a made-up tool name go to Errors, never to Handoffs,
// ToolCalls or the trace; the loop goes on with the state unchanged (R26).
func TestUnknownNamesGoToErrors(t *testing.T) {
	use := func(name string, in map[string]any) map[string]any {
		return map[string]any{"tool_use": map[string]any{"name": name, "input": in}}
	}
	m := &scriptedModel{script: []any{ho("billing-bot"), use("handoff", map[string]any{"worker": nil, "task": "x"}),
		use("write_file", map[string]any{"path": "x"}), "Done."}}
	tools := &stubTools{ops: true}
	run, err := Supervisor(loadTicket(t, "t001"), m, tools, loadPrompts(t))
	if err != nil {
		t.Fatal(err)
	}
	out := run.Output.(SupervisorOutput)
	if len(out.Handoffs) != 0 || len(out.ToolCalls) != 0 || len(tools.names) != 0 || out.Reply != "Done." {
		t.Fatalf("handoffs %+v tool calls %+v tools %v reply %q", out.Handoffs, out.ToolCalls, tools.names, out.Reply)
	}
	want := []DeskError{
		{"supervisor", "handoff", map[string]any{"worker": "billing-bot", "task": "go"}, "Unknown worker: billing-bot. Workers: triage, research, drafter, reviewer"},
		{"supervisor", "handoff", map[string]any{"worker": nil, "task": "x"}, "Unknown worker: null. Workers: triage, research, drafter, reviewer"},
		{"supervisor", "write_file", map[string]any{"path": "x"}, "Unknown tool: write_file"},
	}
	if !reflect.DeepEqual(out.Errors, want) {
		t.Fatalf("errors %+v, want %+v", out.Errors, want)
	}
	for _, r := range run.Trace {
		if r.Kind == "handoff" {
			t.Fatalf("an unknown worker left a handoff record: %+v", r)
		}
	}
}
