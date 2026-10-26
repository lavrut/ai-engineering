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
