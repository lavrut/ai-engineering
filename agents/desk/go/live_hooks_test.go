package patterns

// Task 8's seams on the patterns package: a Model that keeps the run's trace (TraceSink) receives the desks'
// handoff and stop records in order, and the parallel pattern writes its lookup arguments as Python does (R23).

import (
	"strings"
	"testing"
)

// sinkModel is a scriptedModel that also keeps one ordered log: a "model" record per call, plus whatever the
// desk hands it through Record.
type sinkModel struct {
	scriptedModel
	log []TraceRecord
}

func (s *sinkModel) Call(req Request) (Reply, error) {
	s.log = append(s.log, TraceRecord{Agent: req.Agent, Kind: "model", Name: "m"})
	return s.scriptedModel.Call(req)
}

func (s *sinkModel) Record(r TraceRecord) { s.log = append(s.log, r) }

func TestTraceSinkGetsHandoffsInOrder(t *testing.T) {
	m := &sinkModel{scriptedModel: scriptedModel{script: []any{ho("triage"), `{"category": "billing", "priority": "high"}`,
		ho("drafter"), "Draft.", "Done."}}}
	run, err := Supervisor(loadTicket(t, "t001"), m, &stubTools{ops: true}, loadPrompts(t))
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, r := range m.log {
		got = append(got, r.Agent+"/"+r.Kind+"/"+r.Name)
	}
	want := []string{"supervisor/model/m", "triage/model/m", "supervisor/handoff/triage: category",
		"supervisor/model/m", "drafter/model/m", "supervisor/handoff/drafter: draft", "supervisor/model/m"}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("sink log\n got %v\nwant %v", got, want)
	}
	if len(run.Trace) != 2 { // the desk's own list is unchanged: handoff and stop records only
		t.Fatalf("run.Trace has %d records, want 2", len(run.Trace))
	}
}

func TestTraceSinkGetsStopRecords(t *testing.T) {
	script := []any{}
	for range MaxTurns {
		script = append(script, tu("get_ticket", map[string]any{"id": "t001"}))
	}
	m := &sinkModel{scriptedModel: scriptedModel{script: script}}
	if _, err := SingleAgent(loadTicket(t, "t001"), m, &stubTools{}); err != nil {
		t.Fatal(err)
	}
	last := m.log[len(m.log)-1]
	if last.Kind != "stop" || last.Name != "max_turns" || last.Agent != "single" {
		t.Fatalf("last sink record %+v, want the single agent's max_turns stop", last)
	}
}

func TestParallelLookupArgsArePythonStyle(t *testing.T) {
	m := &scriptedModel{script: []any{tu("get_ticket", map[string]any{"id": "t001"}),
		tu("search_tickets", map[string]any{"query": "a<b", "limit": 2}), tu("search_tickets", map[string]any{"query": "x"}), "Sum."}}
	if _, err := Parallel(loadTicket(t, "t001"), m, &stubTools{}, 1); err != nil {
		t.Fatal(err)
	}
	msg := m.reqs[3].Messages[0].Content.(string)
	for _, want := range []string{`get_ticket({"id": "t001"})`, `search_tickets({"limit": 2, "query": "a<b"})`} {
		if !strings.Contains(msg, want) {
			t.Fatalf("summarizer message lacks %s:\n%s", want, msg)
		}
	}
}
