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


def test_summarize_reports_all_tickets_and_warns(capsys):
    test = [dict(TICKET, id="a"), dict(TICKET, id="b")]
    good = lora.prediction_line(test[0], '{"category":"billing","priority":"high"}')
    bad = lora.prediction_line(test[1], "junk")
    lora.summarize([good, bad], test)
    out = capsys.readouterr().out
    assert "1/2 = 50%" in out and "WARNING: 1 of 2" in out
    lora.summarize([lora.prediction_line(test[0], "junk")], test)   # nothing parsed: no traceback
    assert "No output could be parsed" in capsys.readouterr().out


def test_lora_imports_without_the_anthropic_sdk():
    """Colab and the format tests need no SDK: triage_lab imports anthropic only inside a paid call."""
    import subprocess
    code = ("import sys; sys.modules['anthropic'] = None; "     # None makes `import anthropic` raise ImportError
            f"sys.path[:0] = [{str(Path(__file__).resolve().parent)!r}]; "
            "import lora, triage_lab; print('ok', len(triage_lab.load_tickets()))")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok 60"


def test_score_without_a_path_prints_usage(capsys):
    assert lora.main(["--score"]) == 2
    assert "usage: python llms/finetune/lora.py --score predictions.jsonl" in capsys.readouterr().err


class FakeTokenizer:
    """Stands in for a chat tokenizer: one id per character, a chat template with an end-of-turn mark."""
    END = "<|end|>"

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False):
        text = "".join(f"<{m['role']}>{m['content']}{self.END}" for m in messages)
        return text + ("<assistant>" if add_generation_prompt else "")

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": [ord(c) for c in text]}


def test_tokenize_masks_the_prompt_and_labels_only_the_completion():
    tok = FakeTokenizer()
    ex = lora.to_example(TICKET)
    out = lora.tokenize(ex, tok)
    prompt_ids = tok(lora.chat(tok, ex["prompt"]))["input_ids"]
    assert out["input_ids"][:len(prompt_ids)] == prompt_ids               # the prompt is a prefix of the full ids
    assert out["labels"].count(-100) == len(prompt_ids)                    # every prompt token is masked
    labelled = "".join(chr(i) for i in out["labels"] if i != -100)
    assert labelled == ex["completion"] + tok.END                           # only the completion and end-of-turn
    assert len(out["labels"]) == len(out["input_ids"]) == len(out["attention_mask"])
