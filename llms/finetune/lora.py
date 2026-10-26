#!/usr/bin/env python3
"""
lora — fine-tune a small open model on the triage task with LoRA, then grade it
with the same code graders as the rest of the lab.

LoRA freezes the base model and trains two small matrices (rank r) beside selected
layers. Here: Qwen/Qwen2.5-0.5B-Instruct learns to answer a ticket with
{"category": ..., "priority": ...}. It trains on the dev split and is graded on test.

Limits: 3 epochs on 40 dev tickets is only about 15 optimizer steps. This demonstrates
the mechanics; it is not a tuned model. With 20 test tickets the noise band is about
+-22%, so any comparison with the API models is within noise.

Run in Google Colab (Runtime > Change runtime type > T4 GPU), about 10 minutes:
    1. On your machine, zip the repo's llms/ folder (the course repo is private, so
       don't paste a GitHub token into a notebook).
    2. In Colab, upload llms.zip in the file panel, then:
         !unzip -q llms.zip
         !pip install -q -r llms/finetune/requirements.txt
         !python llms/finetune/lora.py
    The first run downloads about 1 GB of model weights from Hugging Face.
    predictions.jsonl is written to the current directory: download it from the file
    panel (or copy it to Drive), because Colab deletes it when the session ends.
It prints accuracy over the parsed outputs and over all test tickets. Re-score a saved file later:
    python llms/finetune/lora.py --score predictions.jsonl
Format tests need no GPU: python -m pytest llms/finetune -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import triage_lab  # noqa: E402  reuse the dataset, graders and report; don't copy them

# ── 1. Configuration ────────────────────────────────────────────────────────
BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
MODEL_NAME = BASE_MODEL + "+lora"      # the name saved on every prediction line
LORA_R = 8                             # rank: how many numbers each adapter may learn
LORA_ALPHA = 16                        # scale; alpha / r = 2 is a common default
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj"]   # attention projections
EPOCHS = 3
SEED = 0                              # fixed so runs are comparable
LEARNING_RATE = 2e-4                   # LoRA tolerates a higher rate than full fine-tuning
MAX_NEW_TOKENS = 32                    # the answer is a short JSON object
OUT_PATH = Path("predictions.jsonl")
SYSTEM = ("You triage support tickets for Lumen, a cloud notes app. Reply with only JSON: "
          '{"category": one of ' + ", ".join(triage_lab.CATEGORIES) +
          ', "priority": one of ' + ", ".join(triage_lab.PRIORITIES) + "}.")


# ── 2. Training examples ────────────────────────────────────────────────────
def to_example(ticket: dict) -> dict:
    """One ticket becomes a prompt and the exact completion we want back (compact JSON)."""
    label = {"category": ticket["category"], "priority": ticket["priority"]}
    return {"prompt": ticket["text"], "completion": json.dumps(label, separators=(",", ":"))}


def parse_completion(text: str) -> dict | None:
    """Pull {"category", "priority"} out of generated text; None if it isn't valid."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("category") not in triage_lab.CATEGORIES or data.get("priority") not in triage_lab.PRIORITIES:
        return None
    return {"category": data["category"], "priority": data["priority"]}


def chat(tokenizer, prompt: str, completion: str | None = None) -> str:
    """Format with the model's own chat template. Without a completion, end on the
    assistant turn marker so the model writes the answer."""
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    if completion is None:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    messages.append({"role": "assistant", "content": completion})
    return tokenizer.apply_chat_template(messages, tokenize=False)


def prediction_line(ticket: dict, text: str) -> dict:
    """Wrap generated text as a saved-response line so triage_lab.grade and report score it."""
    parsed = parse_completion(text)
    return {"key": f"lora:{ticket['id']}",
            "config": {"kind": "triage", "model": MODEL_NAME, "effort": None,
                       "prompt_version": "lora", "rep": 0},
            "case_id": ticket["id"],
            "output": None if parsed is None else {**parsed, "reply": ""},
            "stop_reason": "end_turn",
            "input_tokens": 0, "output_tokens": 0, "latency_ms": 0,
            "error": None if parsed else f"unparseable output: {text[:80]!r}"}


# ── 3. Model and LoRA adapters ──────────────────────────────────────────────
def load_model():
    """Load the base model, then wrap it so only the small LoRA matrices train."""
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, torch_dtype=torch.float32)   # fp32 is steady on a T4 and fine at 0.5B
    config = LoraConfig(r=LORA_R, lora_alpha=LORA_ALPHA, target_modules=TARGET_MODULES,
                        lora_dropout=0.05, task_type="CAUSAL_LM")
    model = get_peft_model(model, config)
    model.print_trainable_parameters()           # well under 1% of the weights
    return model, tokenizer


# ── 4. Training ─────────────────────────────────────────────────────────────
def tokenize(example: dict, tokenizer) -> dict:
    """Loss counts only the completion: prompt tokens are masked with -100."""
    prompt_ids = tokenizer(chat(tokenizer, example["prompt"]), add_special_tokens=False)["input_ids"]
    full_ids = tokenizer(chat(tokenizer, example["prompt"], example["completion"]),
                         add_special_tokens=False)["input_ids"][:512]
    labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids):]
    return {"input_ids": full_ids, "attention_mask": [1] * len(full_ids), "labels": labels[:len(full_ids)]}


def train(model, tokenizer, tickets: list[dict]):
    """40 tickets / batch 4 / accumulate 2 = 5 steps per epoch, so about 15 steps in all:
    enough to see the format and some signal, far too little to call the model tuned."""
    from datasets import Dataset
    from transformers import DataCollatorForSeq2Seq, Trainer, TrainingArguments, set_seed

    set_seed(SEED)

    rows = [to_example(t) for t in tickets]
    data = Dataset.from_list(rows).map(lambda e: tokenize(e, tokenizer), remove_columns=["prompt", "completion"])
    args = TrainingArguments(output_dir="lora-out", num_train_epochs=EPOCHS,
                             per_device_train_batch_size=4, gradient_accumulation_steps=2,
                             learning_rate=LEARNING_RATE, logging_steps=10,
                             save_strategy="no", report_to="none", seed=SEED)
    # The collator pads input_ids and labels to the same length in each batch.
    Trainer(model=model, args=args, train_dataset=data,
            data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True)).train()


# ── 5. Predictions and grading ──────────────────────────────────────────────
def predict(model, tokenizer, tickets: list[dict]) -> list[dict]:
    """Greedy generation (no sampling) for each ticket; one saved-response line each."""
    import torch

    model.eval()
    lines = []
    for ticket in tickets:
        inputs = tokenizer(chat(tokenizer, ticket["text"]), return_tensors="pt",
                           add_special_tokens=False).to(model.device)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
        text = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        lines.append(prediction_line(ticket, text))
    return lines


def summarize(lines: list[dict], test: list[dict]) -> None:
    """Print scores. triage_lab's accuracy skips error lines, so also count every test ticket."""
    by_id = {t["id"]: t for t in test}
    grades = [triage_lab.grade(by_id[l["case_id"]], l) for l in lines]
    bad = sum(g["outcome"] != "ok" for g in grades)
    right = sum(g["both_ok"] for g in grades)
    print(f"correct on all {len(test)} test tickets: {right}/{len(test)} = {right / len(test):.0%}")
    if bad:
        print(f"WARNING: {bad} of {len(lines)} outputs were unparseable; they count as wrong above.")
    try:
        r = triage_lab.report(grades)
    except ValueError:
        print("No output could be parsed, so there is no accuracy over parsed outputs. "
              "Check predictions.jsonl for what the model wrote.")
        return
    print(f"over the {r['scored']} parsed outputs only: category {r['category']:.0%}, "
          f"priority {r['priority']:.0%}, both {r['both']:.0%} (noise band +-{r['band']:.0%})")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    test = triage_lab.split(triage_lab.load_tickets(), "test")
    if argv[:1] == ["--score"]:                  # re-score a saved file, no GPU needed
        summarize(triage_lab.load_jsonl(argv[1]), test)
        return 0
    dev = triage_lab.split(triage_lab.load_tickets(), "dev")
    model, tokenizer = load_model()
    train(model, tokenizer, dev)
    lines = predict(model, tokenizer, test)
    OUT_PATH.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lines), encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(lines)} lines)")
    summarize(lines, test)
    return 0


if __name__ == "__main__":
    sys.exit(main())
