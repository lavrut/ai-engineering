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
