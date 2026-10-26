#!/usr/bin/env python3
"""
triage_inspect — the triage eval from triage_lab, re-run through Inspect AI.

Inspect is a framework for running evals: it owns the dataset, the solver, the
scorers, epochs and the log. The lab owns the prompts, the saved responses and
the graders. This file connects them.

Replay vs live. In replay (the default) the model is `replay/<model>`: a custom
Inspect model API that answers from the lab's saved responses in llms/data/responses/,
so the run is free and identical every time. It never falls back to a paid call;
a request with no saved answer raises an error naming the lab's record command.
In live mode you pass `--model anthropic/<model>` instead and Inspect calls the
API itself. Live mode is documented in the lesson, not run here.

Run (from the repo root, in the venv):
    .venv-inspect/bin/pip install -r llms/inspect/requirements.txt
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))   # import the lab from any cwd

import inspect_ai
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessage,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    ModelUsage,
    modelapi,
)
from inspect_ai.tool import ToolChoice, ToolInfo

from triage_lab import (  # noqa: F401  (later sections use the rest)
    DATA_DIR,
    JUDGE_MODEL,
    JUDGE_SCHEMA,
    RUBRIC_IDS,
    TASK_MODEL,
    TRIAGE_SCHEMA,
    ReplayMiss,
    _judge_system,
    judge_key,
    load_jsonl,
    load_prompts,
    load_tickets,
    response_path,
    triage_key,
)

# ── 1. Configuration ────────────────────────────────────────────────────────
INSPECT_VERSION = "0.3.285"        # the version this lesson was written and checked against
REP_HEADER = "x-replay-rep"        # the solver sends the epoch here, so each epoch replays its own rep

if inspect_ai.__version__ != INSPECT_VERSION:
    warnings.warn(f"this lesson was written for inspect-ai {INSPECT_VERSION}, you have "
                  f"{inspect_ai.__version__}; the replay provider may behave differently")


# ── 2. Dataset ──────────────────────────────────────────────────────────────
def dataset(split: str | None = None, root=DATA_DIR) -> list[Sample]:
    """The lab's tickets as Inspect samples: input is the ticket text, target is the true category.
    Priority, split and tags ride along as metadata. split=None means all 60."""
    tickets = load_tickets(Path(root) / "tickets.jsonl")
    return [Sample(input=t["text"], target=t["category"], id=t["id"],
                   metadata={"priority": t["priority"], "split": t["split"], "tags": t["tags"]})
            for t in tickets if split is None or t["split"] == split]


# ── 3. Replay model API ─────────────────────────────────────────────────────
# Inspect asks a "model" for a completion. This one looks the request up in the
# lab's saved responses instead of calling an API. It finds the line by rebuilding
# the lab's key from the request, so a changed prompt or ticket text is a miss, not a wrong answer.
STOP_REASONS = {"end_turn": "stop", "max_tokens": "max_tokens", "refusal": "content_filter"}


def saved_line(kind, model, prompt_version, effort, key, root) -> dict:
    """The latest saved line for `key` (the latest recording wins, as in the lab), or ReplayMiss."""
    path = response_path(kind, model, prompt_version, effort, root)
    if path.exists():
        for line in reversed(load_jsonl(path)):
            if line["key"] == key:
                return line
    raise ReplayMiss(f"no saved response for {kind} / {model} / {prompt_version}. "
                     f"Record it: python triage_lab.py record ...")


def _text(message: ChatMessage) -> str:
    return message.text


@modelapi(name="replay")
class ReplayAPI(ModelAPI):
    """Model `replay/<model>`; `-M root=<data root>` picks the saved data (default: llms/data)."""

    def __init__(self, model_name: str, base_url: str | None = None, api_key: str | None = None,
                 config: GenerateConfig = GenerateConfig(), root: str | Path = DATA_DIR, **model_args) -> None:
        super().__init__(model_name, base_url, api_key, [], config)
        self.root = Path(root)
        self.prompts = load_prompts(self.root / "prompts.json")

    async def generate(self, input: list[ChatMessage], tools: list[ToolInfo],
                       tool_choice: ToolChoice, config: GenerateConfig) -> ModelOutput:
        system = "\n".join(_text(m) for m in input if m.role == "system")
        user = next((_text(m) for m in reversed(input) if m.role == "user"), "")
        rep = int((config.extra_headers or {}).get(REP_HEADER, 0))   # no header means rep 0

        variant = next((v for v, text in self.prompts["triage"].items() if system == text), None)
        if variant is not None:
            key = triage_key(user, variant, self.model_name, None, rep, self.prompts)
            line = saved_line("triage", self.model_name, variant, None, key, self.root)
        elif system == _judge_system(self.prompts):
            raise NotImplementedError("the judge branch arrives with the grader (Task 3)")
        else:
            raise ReplayMiss(f"no saved response for {self.model_name}: unknown system prompt "
                             f"{system[:60]!r}. Record it: python triage_lab.py record ...")
        return self._output(line)

    def _output(self, line: dict) -> ModelOutput:
        """Turn a saved line into Inspect's output: tokens, stop reason, and the JSON as the completion."""
        if line.get("error"):
            raise RuntimeError(f"saved response errored: {line['error']}")
        content = "" if line["output"] is None else json.dumps(line["output"])
        out = ModelOutput.from_content(model=self.model_name, content=content,
                                       stop_reason=STOP_REASONS.get(line["stop_reason"], "unknown"))
        out.usage = ModelUsage(input_tokens=line["input_tokens"], output_tokens=line["output_tokens"],
                               total_tokens=line["input_tokens"] + line["output_tokens"])
        return out
