"""Format tests for lora.py. Standard library plus triage_lab only: no torch, no GPU."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import lora
import triage_lab

TICKET = {"id": "t001", "text": "Charged twice, please refund.", "category": "billing",
          "priority": "high", "tags": [], "split": "dev"}


def test_example_completion_is_compact_json():
    ex = lora.to_example(TICKET)
    assert ex["completion"] == '{"category":"billing","priority":"high"}'
    assert TICKET["text"] in ex["prompt"]


def test_prompt_does_not_leak_the_label():
    ex = lora.to_example(TICKET)
    assert ex["completion"] not in ex["prompt"]


def test_parse_completion_handles_junk():
    assert lora.parse_completion("category: billing") is None
    assert lora.parse_completion("") is None
    assert lora.parse_completion('{"category":"nope","priority":"high"}') is None
    assert lora.parse_completion('{"category":"bug"}') is None
    assert lora.parse_completion('[1, 2]') is None
    assert lora.parse_completion('Sure! {"category":"bug","priority":"low"} done') == \
        {"category": "bug", "priority": "low"}


def test_prediction_line_is_gradeable():
    line = lora.prediction_line(TICKET, '{"category":"billing","priority":"high"}')
    assert line["case_id"] == "t001" and line["error"] is None
    assert line["config"] == {"kind": "triage", "model": "Qwen/Qwen2.5-0.5B-Instruct+lora",
                              "effort": None, "prompt_version": "lora", "rep": 0}
    assert line["output"] == {"category": "billing", "priority": "high", "reply": ""}
    g = triage_lab.grade(TICKET, line)
    assert g["both_ok"] and g["outcome"] == "ok"
    json.dumps(line)


def test_unparseable_prediction_is_an_error_line():
    line = lora.prediction_line(TICKET, "I think billing")
    assert line["output"] is None and line["error"]
    g = triage_lab.grade(TICKET, line)
    assert g["outcome"] == "error" and not g["both_ok"]
    ok = triage_lab.grade(TICKET, lora.prediction_line(TICKET, '{"category":"billing","priority":"high"}'))
    r = triage_lab.report([g, ok])
    assert r["errors"] == 1 and r["both"] == 1.0
