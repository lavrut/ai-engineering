"""The committed LoRA predictions: one real Colab T4 run of llms/finetune/lora.py on 2026-10-28.

The file sits where the lab's own response_path puts that configuration, and `lora.py --score`
must re-score it to the numbers lesson 9 quotes, so a corrupted or swapped file fails here.
"""
import json
import subprocess
import sys
from pathlib import Path

import triage_lab as lab

LLMS = Path(__file__).resolve().parent.parent.parent
LORA = LLMS / "finetune" / "lora.py"
MODEL = "Qwen/Qwen2.5-0.5B-Instruct+lora"          # lora.MODEL_NAME; lora.py itself needs no import here
PATH = lab.response_path("triage", MODEL, "lora", None)


def test_the_file_is_where_the_lab_looks():
    assert PATH == LLMS / "data" / "responses" / "triage__Qwen" / "Qwen2.5-0.5B-Instruct+lora__lora__default.jsonl"
    assert 'MODEL_NAME = BASE_MODEL + "+lora"' in LORA.read_text("utf-8")
    assert 'BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"' in LORA.read_text("utf-8")


def test_one_line_per_test_ticket_with_the_lora_config():
    lines = lab.load_jsonl(PATH)
    test_ids = sorted(t["id"] for t in lab.split(lab.load_tickets(), "test"))
    assert sorted(l["case_id"] for l in lines) == test_ids
    assert all(l["config"] == {"kind": "triage", "model": MODEL, "effort": None, "prompt_version": "lora", "rep": 0}
               for l in lines)
    assert all(l["error"] is None and l["output"]["reply"] == "" for l in lines)


def test_rescores_to_the_colab_numbers():
    run = subprocess.run([sys.executable, str(LORA), "--score", str(PATH)], capture_output=True, text=True,
                         timeout=120, env={"PATH": "/usr/bin:/bin"})          # no ANTHROPIC_API_KEY
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == [
        "correct on all 20 test tickets: 5/20 = 25%",
        "over the 20 parsed outputs only: category 60%, priority 40%, both 25% (noise band +-22%)",
    ]


def test_report_matches_exactly():
    by_id = {t["id"]: t for t in lab.load_tickets()}
    r = lab.report([lab.grade(by_id[l["case_id"]], l) for l in lab.load_jsonl(PATH)])
    assert (r["scored"], r["category"], r["priority"], r["both"], round(r["band"], 3)) == (20, 0.6, 0.4, 0.25, 0.224)
