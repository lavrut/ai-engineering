"""Tests for agents/derive.py. No API calls, ever.

Every trace here is a labelled test fixture written into tmp_path (never agents/data/traces): a small, made-up set
of runs whose numbers are chosen so that a value taken from the wrong field shows. The ledger (proxy_calls) says
one thing; the run's `spent`, its top-level `cost_usd` and every trace record's `ms` say something else and huge.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents"))

import derive  # noqa: E402

FIXTURE_NOTE = "test fixture (agents/tests/test_derive.py), not a recording"
LONG = "x" * 1000                      # longer than every excerpt cap
DECOY = 999.0                          # spent / cost_usd / record ms: never to be read as cost or latency


# ── fixture builders ────────────────────────────────────────────────────────
def ledger(n: int, *, cost: float = 0.001, ms: int = 100, cache_read: int = 0) -> list[dict]:
    return [{"key": f"{i:064x}", "replayed": False, "ms": ms, "model": "claude-haiku-5-5", "input_tokens": 10,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": cache_read, "output_tokens": 5,
             "cost_usd": cost} for i in range(n)]


def rec(agent: str, kind: str, name: str = "claude-haiku-5-5") -> dict:
    tin = 1 if kind == "model" else None
    return {"agent": agent, "kind": kind, "name": name, "tokens_in": tin, "tokens_out": tin, "ms": 99999}


def write(traces: Path, doc: dict) -> None:
    path = traces / doc["set"] / f"{doc['engine']}-{doc['desk'].removeprefix('pattern:')}-{doc['ticket']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**doc, "fixture": FIXTURE_NOTE}, sort_keys=True), encoding="utf-8")


def base(set_name: str, engine: str, desk: str, ticket: str) -> dict:
    return {"set": set_name, "engine": engine, "desk": desk, "ticket": ticket, "mode": "record",
            "model": "claude-haiku-5-5", "approve": "decline", "elicit": "accept", "errors": [],
            "cost_usd": DECOY, "spent": DECOY, "paid_calls": 0}


def chain_doc(engine: str, ticket: str, *, reply: str = "Hi there.") -> dict:
    set_name = "patterns" if engine == "hand" else "patterns-go"
    return {**base(set_name, engine, "pattern:chain", ticket),
            "trace": [rec("classifier", "model"), rec("writer", "model")],
            "output": {"calls": [{"agent": "classifier", "role": "classify"}, {"agent": "writer", "role": "reply"}],
                       "result": {"category": "billing", "reply": reply}},
            "proxy_calls": ledger(2)}


def parallel_doc(engine: str, ticket: str) -> dict:
    set_name = "patterns" if engine == "hand" else "patterns-go"
    lookups = [{"tool": "get_ticket", "args": {"id": ticket}, "is_error": False},
               {"tool": "search_tickets", "args": {"query": LONG}, "is_error": False}]
    return {**base(set_name, engine, "pattern:parallel", ticket),
            "trace": [rec("lookup-1", "model"), rec("lookup-2", "model"), rec("parallel", "tool", "get_ticket"),
                      rec("parallel", "tool", "search_tickets"), rec("summarizer", "model")],
            "output": {"calls": [{"agent": "lookup-1", "role": "lookup"}, {"agent": "lookup-2", "role": "lookup"},
                                 {"agent": "summarizer", "role": "summarize"}],
                       "result": {"lookups": lookups, "summary": LONG}},
            "proxy_calls": ledger(3)}


def desk_doc(engine: str, desk: str, ticket: str, *, reviews: int = 1, traced_label: bool = True,
             cost: float = 0.001, ms: int = 100) -> dict:
    """A desk run: supervisor -> drafter/reviewer `reviews` times, a search, a refused label_ticket. When
    traced_label is False the refusal is in output.tool_calls only (as the LangGraph and SDK desks log it)."""
    trace = [rec(desk, "model"), rec(desk, "tool", "search_tickets")]
    handoffs = []
    for _ in range(reviews if desk == "supervisor" else 0):
        trace += [rec("drafter", "model"), rec("supervisor", "handoff", "drafter: draft"),
                  rec("reviewer", "model"), rec("supervisor", "handoff", "reviewer: review")]
        handoffs += [{"worker": "drafter", "task": LONG, "changed": {"draft": "d"}, "is_error": False},
                     {"worker": "reviewer", "task": "check", "changed": {"review": {"passed": True}}, "is_error": False}]
    if traced_label:
        trace.append(rec(desk, "tool", "label_ticket"))
    trace.append(rec(desk, "model"))
    models = sum(r["kind"] == "model" for r in trace)
    output = {"calls": [{"agent": desk, "role": "act"}] * models, "reply": "R" * 1000, "stop": "end_turn",
              "tool_calls": [{"agent": desk, "tool": "search_tickets", "args": {"query": LONG}, "is_error": False},
                             {"agent": desk, "tool": "label_ticket", "args": {"id": ticket, "label": "outage"},
                              "is_error": True}]}
    if desk == "supervisor":
        output["handoffs"] = handoffs
    return {**base(f"desk-{engine}", engine, desk, ticket), "trace": trace, "output": output,
            "proxy_calls": ledger(models, cost=cost, ms=ms)}


def scores_doc(tickets: list[str]) -> dict:
    row = {"n": len(tickets), "complete": len(tickets), "status": "complete", "success_rate": 0.5, "band": 0.27735,
           "runs": {t: {"task_success": t != tickets[0], "cost_usd": 0.0, "ms": 0, "fixture": True} for t in tickets}}
    return {"about": {}, "gate": {"engine": None, "desk": None, "reason": "none qualifies"},
            "scores": {e: {d: row for d in derive.DESKS} for e in derive.ENGINES}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A small fixture world: 2 tickets in the desk sets, every pattern trace for chain and parallel on t001,
    hand only for t007, and desk-sdk missing its supervisor t061 run."""
    tickets = ["t001", "t061"]
    monkeypatch.setattr(derive, "DESK_TICKETS", tuple(tickets))
    monkeypatch.setattr(derive, "PATTERN_TICKETS", ("t001", "t007"))
    monkeypatch.setattr(derive, "TRACE_TICKETS", ("t001", "t061"))
    traces, store, out = tmp_path / "traces", tmp_path / "recordings", tmp_path / "derived"
    for engine in ("hand", "hand-go"):
        write(traces, chain_doc(engine, "t001"))
        write(traces, parallel_doc(engine, "t001"))
    write(traces, chain_doc("hand", "t007"))
    for engine in derive.ENGINES:
        for desk in derive.DESKS:
            for t in tickets:
                if (engine, desk, t) == ("sdk", "supervisor", "t061"):
                    continue
                reviews = 3 if (desk == "supervisor" and t == "t061") else 1
                write(traces, desk_doc(engine, desk, t, reviews=reviews, traced_label=engine in ("hand", "hand-go"),
                                       cost=0.001 if desk == "single" else 0.004, ms=100 if desk == "single" else 300))
    for s, n in (("patterns", 3), ("desk-hand", 8)):     # the desk-hand store holds every key its runs use
        (store / s).mkdir(parents=True)
        for i in range(n):
            (store / s / f"{i:064x}.json").write_text("{}", encoding="utf-8")
    (store / "patterns" / f"{'e' * 64}.json").write_text("{}", encoding="utf-8")   # superseded: no run uses it
    out.mkdir()
    (out / "engine_scores.json").write_text(json.dumps(scores_doc(tickets)), encoding="utf-8")
    return {"traces": traces, "store": store, "out": out}


def build(world) -> dict[str, dict]:
    scores = json.loads((world["out"] / "engine_scores.json").read_text(encoding="utf-8"))
    return derive.build(world["traces"], world["store"], scores)


def run_main(world, *extra, evals_check=lambda: 0) -> int:
    return derive.main(list(extra), traces_dir=world["traces"], store_dir=world["store"], out_dir=world["out"],
                       evals_check=evals_check)


def walk_strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from walk_strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk_strings(v)


# ── shape ───────────────────────────────────────────────────────────────────
def test_builds_the_four_files_each_with_about(world):
    docs = build(world)
    assert sorted(docs) == sorted(derive.FILES)
    for name, doc in docs.items():
        about = doc["about"]
        assert {"what", "sources", "budget", "rulings"} <= set(about), name
        assert {"R62", "R64", "R66"} <= set(about["rulings"]), name


def test_patterns_shape_and_go_parity_note(world):
    doc = build(world)["patterns.json"]
    run = doc["patterns"]["chain"]["t001"]["hand"]
    assert [s["kind"] for s in run["steps"]] == ["model", "model"]
    assert [s["agent"] for s in run["steps"]] == ["classifier", "writer"]
    assert run["steps"][0]["result"] == "billing" and run["steps"][1]["result"] == "Hi there."
    assert run["calls"] == 2 and run["reply"] == "Hi there."
    par = doc["patterns"]["parallel"]["t001"]["hand-go"]
    assert [s["kind"] for s in par["steps"]] == ["model", "model", "tool", "tool", "model"]
    assert par["steps"][3]["name"] == "search_tickets" and len(par["steps"][3]["args"]) <= derive.ARGS_CAP
    note = doc["notes"]["chain"]
    assert note["same_calls"] is True and note["by_ticket"]["t001"] == {"hand": 2, "hand-go": 2}
    assert set(doc["patterns"]) == set(derive.PATTERNS)


def test_traces_shape_handoffs_writes_and_scorer_row(world):
    doc = build(world)["traces.json"]
    run = doc["runs"]["supervisor"]["hand"]["t001"]
    kinds = [s["kind"] for s in run["steps"]]
    assert kinds.count("model") == run["totals"]["calls"] == 4
    assert "handoff" in kinds and "tool" in kinds
    ho = next(s for s in run["steps"] if s["kind"] == "handoff")
    assert ho["worker"] == "drafter" and len(ho["task"]) <= derive.ARGS_CAP
    label = next(s for s in run["steps"] if s.get("name") == "label_ticket")
    assert label["write"] == "refused" and label["traced"] is True
    assert run["score"]["fixture"] is True and run["score"]["task_success"] is False
    # an engine that logs the refused write only in output.tool_calls still shows it, marked as placed by us
    lg = doc["runs"]["single"]["langgraph"]["t001"]["steps"]
    placed = [s for s in lg if s.get("name") == "label_ticket"]
    assert len(placed) == 1 and placed[0]["traced"] is False and placed[0]["write"] == "refused"
    assert lg[-1]["kind"] == "model"                       # placed before the final reply call


def test_traces_picks_the_longest_supervisor_loop(world):
    doc = build(world)["traces.json"]
    assert doc["tickets"] == ["t001", "t061"]
    assert doc["loop_ticket"] == "t061"
    assert "t061" in doc["about"]["tickets"]


def test_cost_compare_shape_and_ratios(world):
    doc = build(world)["cost_compare.json"]
    row = doc["engines"]["hand"]["single"]
    assert row["n"] == 2 and row["calls_per_ticket"] == {"mean": 2.0, "min": 2, "max": 2}
    assert row["mean_cost_usd"] == pytest.approx(0.002) and row["median_cost_usd"] == pytest.approx(0.002)
    assert row["p50_ms"] == 200 and row["p90_ms"] == 200
    assert row["tokens"]["in"] == {"sum": 40, "mean": 20.0}
    sup = doc["engines"]["hand"]["supervisor"]
    assert sup["calls_per_ticket"] == {"mean": 6.0, "min": 4, "max": 8} and sup["p90_ms"] > sup["p50_ms"]
    assert row["success_rate"] == 0.5 and row["band"] == 0.27735
    assert [r["ticket"] for r in row["tickets"]] == ["t001", "t061"]
    assert row["tickets"][0]["success"] is False
    ratio = doc["engines"]["hand"]["supervisor_vs_single"]
    assert ratio["cost"] > 1 and ratio["latency"] > 1 and ratio["success_delta"] == 0.0


def test_sets_shape(world):
    doc = build(world)["sets.json"]
    pat = doc["sets"]["patterns"]
    # R67: recordings = distinct ledger keys the runs used (3: keys 0-2), orphans = store files no run uses (1)
    assert pat["engine"] == "hand" and pat["recordings"] == 3 and pat["orphans"] == 1 and pat["complete"] is False
    assert pat["runs"] == 3 and pat["model_calls"] == 7
    assert pat["recorded_cost_usd"] == pytest.approx(0.007)
    assert pat["distinct_recorded_cost_usd"] == pytest.approx(0.003)
    hand = doc["sets"]["desk-hand"]
    assert hand["complete"] is True and hand["recordings"] == 8 and hand["orphans"] == 0
    sdk = doc["sets"]["desk-sdk"]
    # t061 (8 calls) missing; no store dir, so its 4 referenced keys are missing, not counted as recordings
    assert sdk["complete"] is False and sdk["recordings"] == 0 and sdk["missing"] == 4 and sdk["orphans"] == 0
    assert pat["missing"] == 0 and hand["missing"] == 0
    # this world has no judge store: the judge requests its scores ask for are all missing, none counted
    assert doc["judge"]["recordings"] == 0 and doc["judge"]["missing"] == 2
    assert "missing" in doc["about"]
    assert "orphans" in doc["about"] and "distinct" in doc["about"]["recordings"]
    assert doc["sets"]["desk-hand"]["replay"] == "python agents/run.py --set desk-hand"


# ── excerpts ────────────────────────────────────────────────────────────────
def test_excerpt_caps(world):
    docs = build(world)
    par = docs["patterns.json"]["patterns"]["parallel"]["t001"]["hand"]
    assert len(par["reply"]) == derive.REPLY_CAP and par["reply"].endswith("…")
    assert all(len(s.get("result") or "") <= derive.STEP_CAP for s in par["steps"])
    for desk in derive.DESKS:
        for engine in derive.ENGINES:
            for run in docs["traces.json"]["runs"][desk][engine].values():
                if run.get("recorded") is False:
                    continue
                assert len(run["reply"]) <= derive.REPLY_CAP
                for s in run["steps"]:
                    assert len(s.get("args") or "") <= derive.ARGS_CAP
                    assert len(s.get("task") or "") <= derive.ARGS_CAP


def test_excerpt_function():
    assert derive.excerpt("short", 10) == "short"
    assert derive.excerpt("a  b\n c", 10) == "a b c"
    cut = derive.excerpt("y" * 50, 10)
    assert len(cut) == 10 and cut.endswith("…")
    assert derive.excerpt(None, 10) is None


def test_sets_report_a_referenced_key_with_no_store_file(world, tmp_path):
    """A recording a trace references but the store no longer holds is reported as missing, never counted."""
    store = tmp_path / "recordings" / "desk-hand"
    victim = sorted(store.glob("*.json"))[0]
    victim.unlink()
    hand = build(world)["sets.json"]["sets"]["desk-hand"]
    assert hand["recordings"] == 7 and hand["missing"] == 1 and hand["orphans"] == 0


@pytest.mark.parametrize("text", [
    "Here is the fix: ```python\n" + "x = 1\n" * 60 + "``` and that is all.",
    "```\n" + "line\n" * 80 + "```",
    "Before ```a``` then ```sh\n" + "echo hi\n" * 40 + "```",
])
def test_excerpt_never_leaves_a_code_fence_open(text):
    cut = derive.excerpt(text, 120)
    assert len(cut) <= 120 and cut.count("```") % 2 == 0, cut
    assert not cut.rstrip("…").endswith("`") or cut.endswith("```"), cut   # no half fence marker left at the cut


def test_excerpt_with_closed_fences_is_unchanged():
    text = "Run ```ls``` first. " + "word " * 60
    assert derive.excerpt(text, 120) == derive.re.sub(r"\s+", " ", text).strip()[:119].rstrip() + "…"


# ── not recorded yet ────────────────────────────────────────────────────────
def test_missing_trace_is_recorded_false_with_command(world):
    docs = build(world)
    pat = docs["patterns.json"]["patterns"]["chain"]["t007"]["hand-go"]
    assert pat == {"recorded": False, "command": "python agents/run.py --engine hand-go --desk pattern:chain "
                                                 "--tickets t007 --mode record --max-cost 10"}
    tr = docs["traces.json"]["runs"]["supervisor"]["sdk"]["t061"]
    assert tr["recorded"] is False and "--engine sdk --desk supervisor --tickets t061" in tr["command"]
    cc = docs["cost_compare.json"]["engines"]["sdk"]["supervisor"]
    assert cc["recorded"] is False and cc["missing"] == ["t061"] and "--tickets t061" in cc["command"]
    assert docs["cost_compare.json"]["engines"]["sdk"]["supervisor_vs_single"] is None
    assert docs["patterns.json"]["notes"]["chain"]["same_calls"] is True   # t007 has only one side: not compared
    assert "mean_cost_usd" not in json.dumps(cc)


def test_empty_world_is_all_recorded_false(tmp_path):
    docs = derive.build(tmp_path / "none", tmp_path / "none", None)
    cell = docs["patterns.json"]["patterns"]["route"]["t001"]["hand"]
    assert cell["recorded"] is False and cell["command"].startswith("python agents/run.py")
    assert docs["sets.json"]["sets"]["desk-sdk"]["runs"] == 0
    assert docs["sets.json"]["sets"]["desk-sdk"]["recorded_cost_usd"] is None


# ── cost and latency come from the ledger only (R62, R64) ───────────────────
def test_cost_and_latency_from_proxy_calls_not_spent_or_ms(world):
    docs = build(world)
    text = "".join(derive.render(d) for d in docs.values())
    assert "999" not in text and "99999" not in text
    run = docs["patterns.json"]["patterns"]["chain"]["t001"]["hand"]
    assert run["cost_usd"] == pytest.approx(0.002) and run["ms"] == 200
    assert run["steps"][0]["cost_usd"] == pytest.approx(0.001) and run["steps"][0]["ms"] == 100
    assert run["steps"][0]["tokens_in"] == 10 and run["steps"][0]["tokens_out"] == 5
    sup = docs["traces.json"]["runs"]["supervisor"]["hand"]["t001"]["totals"]
    assert sup["cost_usd"] == pytest.approx(0.016) and sup["ms"] == 1200


def test_no_ledger_means_none_never_a_fallback(world):
    doc = chain_doc("hand", "t001")
    doc["proxy_calls"] = []
    write(world["traces"], doc)
    run = build(world)["patterns.json"]["patterns"]["chain"]["t001"]["hand"]
    assert run["cost_usd"] is None and run["ms"] is None
    assert run["steps"][0]["cost_usd"] is None


# ── determinism, budget, --check ────────────────────────────────────────────
def test_two_runs_byte_equal(world, capsys):
    assert run_main(world) == 0
    first = {n: (world["out"] / n).read_bytes() for n in derive.FILES}
    assert run_main(world) == 0
    assert first == {n: (world["out"] / n).read_bytes() for n in derive.FILES}
    for n, b in first.items():
        text = b.decode("utf-8")
        assert text.endswith("}\n") and json.loads(text)
        assert text == json.dumps(json.loads(text), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    out = capsys.readouterr().out
    for n in derive.FILES:
        assert n in out
    assert "total" in out


def test_no_absolute_paths_or_secrets(world):
    for name, doc in build(world).items():
        for s in walk_strings(doc):
            assert not s.startswith("/") and str(world["traces"]) not in s and "sk-ant" not in s, (name, s)


def test_check_passes_when_fresh_and_fails_naming_the_file_on_drift(world, capsys):
    assert run_main(world) == 0
    assert run_main(world, "--check") == 0
    path = world["out"] / "traces.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"t001"', '"t999"', 1), encoding="utf-8")
    capsys.readouterr()
    assert run_main(world, "--check") == 1
    assert "traces.json" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8").count('"t999"') == 1      # --check never rewrites


def test_check_fails_when_a_file_is_missing(world, capsys):
    assert run_main(world) == 0
    (world["out"] / "sets.json").unlink()
    assert run_main(world, "--check") == 1
    assert "sets.json" in capsys.readouterr().out


def test_check_fails_when_engine_scores_check_fails(world, capsys):
    assert run_main(world) == 0
    def drifted() -> int:                  # stands in for run_evals --check, which names its own file
        print("engine_scores.json has drifted from the traces")
        return 1
    capsys.readouterr()
    assert run_main(world, "--check", evals_check=drifted) == 1
    assert capsys.readouterr().out.count("engine_scores.json") == 1      # named once, not twice


def test_over_budget_fails(world, monkeypatch, capsys):
    monkeypatch.setattr(derive, "BUDGET_BYTES", 100)
    assert run_main(world) == 1
    assert "budget" in capsys.readouterr().out


def test_engine_scores_untouched(world):
    before = (world["out"] / "engine_scores.json").read_bytes()
    assert run_main(world) == 0
    assert (world["out"] / "engine_scores.json").read_bytes() == before
