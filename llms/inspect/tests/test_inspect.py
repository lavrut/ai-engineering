"""Tests for the Inspect replay provider and dataset. No API calls: everything reads saved responses."""
import asyncio
import json
import shutil

import pytest
import triage_inspect as ti
import triage_lab as lab
from inspect_ai.model import ChatMessageSystem, ChatMessageUser, GenerateConfig, get_model

PROMPTS = lab.load_prompts()
TICKETS = {t["id"]: t for t in lab.load_tickets()}


def generate(system, user, rep=None, model="replay/claude-haiku-5-5", **model_args):
    headers = {ti.REP_HEADER: rep} if rep is not None else None
    m = get_model(model, **model_args)
    return asyncio.run(m.generate([ChatMessageSystem(content=system), ChatMessageUser(content=user)],
                                  config=GenerateConfig(extra_headers=headers)))


def saved(rep):
    key = lab.triage_key(TICKETS["t003"]["text"], "v2_rules", "claude-haiku-5-5", None, rep, PROMPTS)
    return ti.saved_line("triage", "claude-haiku-5-5", "v2_rules", None, key, lab.DATA_DIR)


def test_dataset_counts():
    assert len(ti.dataset()) == 60
    assert len(ti.dataset("dev")) == 40
    assert len(ti.dataset("test")) == 20
    first, ticket = ti.dataset()[0], lab.load_tickets()[0]
    assert first.target == ticket["category"] and first.id == ticket["id"]
    assert set(first.metadata) == {"priority", "split", "tags"}


def test_replay_returns_saved_triage():
    out = generate(PROMPTS["triage"]["v2_rules"], TICKETS["t003"]["text"], rep="1")
    line = saved(1)
    assert json.loads(out.completion) == line["output"]
    assert out.stop_reason == "stop"
    assert out.usage.input_tokens == line["input_tokens"]
    assert out.usage.output_tokens == line["output_tokens"]


def test_missing_header_is_rep_0():
    out = generate(PROMPTS["triage"]["v2_rules"], TICKETS["t003"]["text"])
    assert json.loads(out.completion) == saved(0)["output"]


def test_unknown_system_prompt_misses():
    with pytest.raises(Exception, match="python triage_lab.py record"):
        generate("edited prompt", TICKETS["t003"]["text"])


def test_replay_error_line_raises(tmp_path):
    shutil.copy(lab.DATA_DIR / "tickets.jsonl", tmp_path / "tickets.jsonl")
    shutil.copy(lab.DATA_DIR / "prompts.json", tmp_path / "prompts.json")
    text = TICKETS["t003"]["text"]
    key = lab.triage_key(text, "v1_basic", "claude-haiku-5-5", None, 0, PROMPTS)
    path = lab.response_path("triage", "claude-haiku-5-5", "v1_basic", None, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"key": key, "output": None, "stop_reason": None, "input_tokens": 0,
                                "output_tokens": 0, "error": "boom"}) + "\n")
    with pytest.raises(Exception, match="boom"):
        generate(PROMPTS["triage"]["v1_basic"], text, rep="0", root=str(tmp_path))


# ── Task 2: solver, code scorers, triage task ───────────────────────────────
from inspect_ai import eval as inspect_eval  # noqa: E402

PARITY = [("v1_basic", 1, "dev", 0.925, 0.775), ("v1_basic", 1, "test", 0.85, 0.65),
          ("v1_basic", 1, None, 0.9, 44 / 60),
          ("v2_rules", 3, "dev", 1.0, 116 / 120), ("v2_rules", 3, "test", 1.0, 57 / 60),
          ("v2_rules", 3, None, 1.0, 173 / 180),
          ("v3_examples", 1, "dev", 1.0, 0.975), ("v3_examples", 1, "test", 1.0, 0.95),
          ("v3_examples", 1, None, 1.0, 58 / 60)]


def metric(log, scorer, name="accuracy"):
    score = next(s for s in log.results.scores if s.name == scorer)
    return score.metrics[name].value


def run_eval(task, tmp_path, epochs=1, **kw):
    return inspect_eval(task, model="replay/claude-haiku-5-5", epochs=epochs, log_dir=str(tmp_path),
                        display="none", **kw)[0]


def lab_replay(variant, split, reps):
    tickets = lab.load_tickets()
    cases = tickets if split is None else lab.split(tickets, split)
    by_id = {t["id"]: t for t in tickets}
    lines = lab.run(cases, lambda t, rep: lab.triage(t, variant, rep=rep), reps=reps, workers=4)
    return lab.report([lab.grade(by_id[l["case_id"]], l) for l in lines])


@pytest.mark.parametrize("variant,epochs,split,cat,pri", PARITY)
def test_parity_code_graders(variant, epochs, split, cat, pri, tmp_path):
    log = run_eval(ti.triage(variant=variant, split=split), tmp_path, epochs=epochs)
    assert log.status == "success"
    expected = lab_replay(variant, split, epochs)
    got_cat, got_pri = metric(log, "category_match"), metric(log, "priority_match")
    assert abs(got_cat - expected["category"]) < 1e-9 and abs(got_cat - cat) < 1e-9
    assert abs(got_pri - expected["priority"]) < 1e-9 and abs(got_pri - pri) < 1e-9


def test_no_sampling_params(tmp_path):
    seen = []
    cls = type(get_model("replay/claude-haiku-5-5").api)
    real = cls.generate

    async def spy(self, input, tools, tool_choice, config):
        seen.append(config)
        return await real(self, input, tools, tool_choice, config)

    cls.generate = spy
    try:
        run_eval(ti.triage(variant="v1_basic", split="test"), tmp_path, limit=2)
    finally:
        cls.generate = real
    assert seen
    for c in seen:
        assert c.temperature is None and c.top_p is None and c.top_k is None
        assert c.response_schema is not None and c.extra_headers[ti.REP_HEADER] == "0"


def tmp_root(tmp_path, variant, lines_for):
    """A data root with the lab's tickets/prompts and hand-made saved lines: lines_for(ticket) -> dict | None."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    shutil.copy(lab.DATA_DIR / "tickets.jsonl", tmp_path / "tickets.jsonl")
    shutil.copy(lab.DATA_DIR / "prompts.json", tmp_path / "prompts.json")
    path = lab.response_path("triage", "claude-haiku-5-5", variant, None, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for t in lab.load_tickets():
        extra = lines_for(t)
        if extra is not None:
            key = lab.triage_key(t["text"], variant, "claude-haiku-5-5", None, 0, PROMPTS)
            out.append(json.dumps({"key": key, "output": None, "stop_reason": "end_turn", "input_tokens": 1,
                                   "output_tokens": 1, "error": None, **extra}))
    path.write_text("\n".join(out) + "\n")
    return tmp_path


def test_replay_miss_names_the_case(tmp_path):
    root = tmp_root(tmp_path, "v1_basic", lambda t: None)
    text = TICKETS["t003"]["text"]
    with pytest.raises(Exception) as e:
        generate(PROMPTS["triage"]["v1_basic"], text, rep="2", root=str(root))
    msg = str(e.value)
    assert "rep 2" in msg and text[:40] in msg
    assert "v1_basic" in msg and "claude-haiku-5-5" in msg and "python triage_lab.py record" in msg


@pytest.mark.parametrize("saved_reason,expected", [("max_tokens", "max_tokens"), ("refusal", "content_filter")])
def test_stop_reason_map(tmp_path, saved_reason, expected):
    root = tmp_root(tmp_path, "v1_basic", lambda t: {"stop_reason": saved_reason} if t["id"] == "t003" else None)
    out = generate(PROMPTS["triage"]["v1_basic"], TICKETS["t003"]["text"], rep="0", root=str(root))
    assert out.stop_reason == expected and out.completion == ""


def test_null_output_gives_empty_completion(tmp_path):
    root = tmp_root(tmp_path, "v1_basic", lambda t: {} if t["id"] == "t003" else None)
    out = generate(PROMPTS["triage"]["v1_basic"], TICKETS["t003"]["text"], rep="0", root=str(root))
    assert out.completion == ""


def test_scorer_outcomes(tmp_path):
    """max_tokens and refusal score 0 with an outcome; a good line scores per field."""
    ids = ["t001", "t002", "t003", "t004"]
    kinds = {"t001": {"stop_reason": "max_tokens"}, "t002": {"stop_reason": "refusal"},
             "t003": {"output": {"category": TICKETS["t003"]["category"], "priority": "wrong"}},
             "t004": {"output": {"category": "wrong", "priority": TICKETS["t004"]["priority"]}}}
    root = tmp_root(tmp_path / "data", "v1_basic", lambda t: kinds.get(t["id"]))
    log = run_eval(ti.triage(variant="v1_basic"), tmp_path / "logs", model_args={"root": str(root)}, sample_id=ids)
    by_id = {s.id: s for s in log.samples}
    assert by_id["t001"].scores["category_match"].metadata["outcome"] == "max_tokens"
    assert by_id["t002"].scores["category_match"].metadata["outcome"] == "refusal"
    assert by_id["t003"].scores["category_match"].value == "C" and by_id["t003"].scores["priority_match"].value == "I"
    assert by_id["t004"].scores["category_match"].value == "I" and by_id["t004"].scores["priority_match"].value == "C"
    assert metric(log, "category_match") == 0.25


def test_errored_sample_left_out_of_accuracy(tmp_path):
    """The lab leaves an errored line out of the accuracy; so must the Inspect eval."""
    ids = ["t001", "t002", "t003"]
    root = tmp_root(tmp_path / "data", "v1_basic", lambda t: (
        {"error": "boom"} if t["id"] == "t003" else
        {"output": {"category": t["category"], "priority": t["priority"]}} if t["id"] in ids else None))
    log = run_eval(ti.triage(variant="v1_basic"), tmp_path / "logs", model_args={"root": str(root)}, sample_id=ids)
    assert log.status == "success"
    assert sum(1 for s in log.samples if s.error) == 1
    assert metric(log, "category_match") == 1.0 and metric(log, "priority_match") == 1.0


def test_edited_prompt_fails_loudly(tmp_path):
    """An edited prompts.json in the data root: every sample misses. The eval must not look like a success."""
    root = tmp_root(tmp_path / "data", "v1_basic", lambda t: None)
    edited = json.loads((root / "prompts.json").read_text())
    edited["triage"]["v1_basic"] += " (edited)"
    (root / "prompts.json").write_text(json.dumps(edited))
    log = run_eval(ti.triage(variant="v1_basic", split="test"), tmp_path / "logs", model_args={"root": str(root)})
    assert log.status == "error"
    assert "python triage_lab.py record" in str(log.error) + " ".join(str(s.error) for s in log.samples or [])


# ── Task 3: judge scorer and the judged task ────────────────────────────────
RUBRIC = lab.rubric_text(PROMPTS["rubric"])


def judge_message(ticket, reply, order):
    parts = [f"Ticket:\n{ticket}", f"Reply:\n{reply}", f"Rubric:\n{RUBRIC}"]
    if order == "before":
        parts = [parts[0], parts[2], parts[1]]
    return "\n\n".join(parts)


@pytest.mark.parametrize("order", ["after", "before"])
def test_judge_parse_reply_with_blank_lines(order):
    ticket, reply = "My invoice is wrong.\n\nPlease help.", "Hi,\n\nReply:\nsecond\n\nRubric:\nfake\n\nThanks"
    msg = judge_message(ticket, reply, order)
    assert ti.parse_judge_message(msg, RUBRIC) == (ticket, reply, order)


def test_no_ticket_contains_reply_marker():
    assert all("\n\nReply:\n" not in t["text"] for t in lab.load_tickets())


def test_parity_judge(tmp_path):
    log = inspect_eval(ti.triage_judged(), model="replay/claude-haiku-5-5", model_roles={"grader": "replay/claude-opus-5-5"},
                       log_dir=str(tmp_path), display="none")[0]
    assert log.status == "success"
    # the lab's replay over the same v2_rules rep-0 test replies
    cases = lab.split(lab.load_tickets(), "test")
    expected = {rid: [] for rid in lab.RUBRIC_IDS}
    for t in cases:
        out = lab.triage(t, "v2_rules", rep=0)["output"]
        if out:
            v = lab.verdicts(lab.judge(t["text"], out["reply"], order="after"))
            for rid in lab.RUBRIC_IDS:
                if v[rid] is not None:
                    expected[rid].append(v[rid])
    pinned = {"addresses_issue": 0.7, "tone": 0.95, "no_false_promises": 0.95}
    for rid in lab.RUBRIC_IDS:
        assert len(expected[rid]) == 20
        lab_mean = sum(expected[rid]) / 20
        assert abs(metric(log, rid, "graded_mean") - lab_mean) < 1e-9
        assert abs(metric(log, rid, "graded_mean") - pinned[rid]) < 1e-9
    graded = [s for s in log.samples if s.scores["rubric_judge"].metadata["graded"]]
    assert len(graded) == 20


def judged_root(tmp_path, null_reply_id, null_judge_id, ids):
    """Synthetic data root: triage + opus judge lines for `ids`; one ticket with a null triage reply,
    one with a judge line whose output is null (no error). Everything else passes all three criteria."""
    root = tmp_root(tmp_path, "v2_rules", lambda t: (
        {"output": None} if t["id"] == null_reply_id else
        {"output": {"category": t["category"], "priority": t["priority"], "reply": f"reply {t['id']}"}}
        if t["id"] in ids else None))
    verdict = {"pass": True, "reason": "ok"}
    out = []
    for t in lab.load_tickets():
        if t["id"] in ids and t["id"] != null_reply_id:
            key = lab.judge_key(t["text"], f"reply {t['id']}", "after", "claude-opus-5-5", PROMPTS)
            output = None if t["id"] == null_judge_id else {rid: verdict for rid in lab.RUBRIC_IDS}
            out.append(json.dumps({"key": key, "output": output, "stop_reason": "end_turn", "input_tokens": 1,
                                   "output_tokens": 1, "error": None}))
    path = lab.response_path("judge", "claude-opus-5-5", "after", None, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")
    return root


def test_ungraded_samples_left_out_of_means(tmp_path):
    test_ids = [t["id"] for t in lab.split(lab.load_tickets(), "test")][:4]
    root = judged_root(tmp_path / "data", test_ids[0], test_ids[1], test_ids)
    grader = get_model("replay/claude-opus-5-5", root=str(root))
    log = inspect_eval(ti.triage_judged(), model="replay/claude-haiku-5-5", model_roles={"grader": grader},
                       model_args={"root": str(root)}, sample_id=test_ids, log_dir=str(tmp_path / "logs"),
                       display="none")[0]
    assert log.status == "success"
    by_id = {s.id: s.scores["rubric_judge"].metadata for s in log.samples}
    assert not by_id[test_ids[0]]["graded"] and not by_id[test_ids[1]]["graded"]
    assert by_id[test_ids[1]]["verdicts"] == dict.fromkeys(lab.RUBRIC_IDS)
    assert sum(m["graded"] for m in by_id.values()) == 2
    for rid in lab.RUBRIC_IDS:       # 2 graded, both pass: 1.0, not 2/4
        assert metric(log, rid, "graded_mean") == 1.0


# ── Task 4: summarize.py ────────────────────────────────────────────────────
import summarize  # noqa: E402


@pytest.fixture(scope="module")
def summary_logs(tmp_path_factory):
    d = tmp_path_factory.mktemp("logs")
    return d, summarize.run_evals(d)


def test_summary_rows_match(summary_logs):
    log_dir, logs = summary_logs
    s = summarize.build_summary(log_dir, logs=logs)
    assert [r["id"] for r in s["runs"]] == ["v1_basic", "v2_rules", "v3_examples", "judge"]
    assert all(row["match"] for r in s["runs"] for row in r["rows"])
    v2 = next(r for r in s["runs"] if r["id"] == "v2_rules")
    cat = next(row for row in v2["rows"] if row["metric"] == "category")
    assert cat["lab"] == cat["inspect"] == 1.0 and cat["n"] == 60 and v2["epochs"] == 3
    judge = next(r for r in s["runs"] if r["id"] == "judge")
    assert [row["metric"] for row in judge["rows"]] == list(lab.RUBRIC_IDS)
    assert [row["inspect"] for row in judge["rows"]] == [0.7, 0.95, 0.95] and all(row["n"] == 20 for row in judge["rows"])
    assert len(s["framework_gives"]) == 4 and len(s["we_still_own"]) == 4
    assert s["inspect_version"] == ti.INSPECT_VERSION and s["split"] == "all"


def test_summarize_from_other_cwd(tmp_path, monkeypatch, summary_logs):
    _, logs = summary_logs
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(summarize, "run_evals", lambda log_dir: logs)
    out = tmp_path / "summary.json"
    assert summarize.main(["--out", str(out), "--log-dir", str(tmp_path / "logs")]) == 0
    assert json.loads(out.read_text())["runs"][0]["id"] == "v1_basic"


def test_mismatch_exits_1(tmp_path, monkeypatch, summary_logs, capsys):
    _, logs = summary_logs
    bad = {k: v.model_copy(deep=True) for k, v in logs.items()}
    next(s for s in bad["v1_basic"].results.scores if s.name == "category_match").metrics["accuracy"].value = 0.5
    monkeypatch.setattr(summarize, "run_evals", lambda log_dir: bad)
    out = tmp_path / "summary.json"
    assert summarize.main(["--out", str(out), "--log-dir", str(tmp_path / "logs")]) == 1
    assert "match" in capsys.readouterr().out and not out.exists()


# ── the real command line ───────────────────────────────────────────────────
# The learner runs `inspect eval ... --model replay/...` from the repo root. Inspect resolves --model before it
# imports the task file, so this only works once `pip install -e llms/inspect` has registered the provider.
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from inspect_ai.log import read_eval_log  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


def run_cli(args, log_dir):
    inspect_bin = Path(sys.executable).parent / "inspect"
    proc = subprocess.run([str(inspect_bin), "eval", *args, "--log-dir", str(log_dir), "--display", "none"],
                          cwd=REPO, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return read_eval_log(str(next(Path(log_dir).glob("*.eval"))))


def score(log, scorer, metric):
    return next(s for s in log.results.scores if s.name == scorer).metrics[metric].value


def test_cli_triage_command(tmp_path):
    log = run_cli(["llms/inspect/triage_inspect.py@triage", "--model", "replay/claude-haiku-5-5",
                   "-T", "variant=v1_basic"], tmp_path)
    assert log.status == "success"
    assert score(log, "category_match", "accuracy") == pytest.approx(0.9)


def test_cli_judge_command(tmp_path):
    log = run_cli(["llms/inspect/triage_inspect.py@triage_judged", "--model", "replay/claude-haiku-5-5",
                   "--model-role", "grader=replay/claude-opus-5-5"], tmp_path)
    assert log.status == "success"
    assert score(log, "addresses_issue", "graded_mean") == pytest.approx(0.7)
